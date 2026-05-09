import base64
import binascii
import json
import mimetypes
import random
import re
import ssl
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

try:
    import paho.mqtt.client as mqtt
    from PyQt6.QtCore import (
        QBuffer,
        QByteArray,
        QIODevice,
        QObject,
        QSize,
        Qt,
        QTimer,
        pyqtSignal,
    )
    from PyQt6.QtGui import QPixmap
    from PyQt6.QtWidgets import (
        QApplication,
        QFileDialog,
        QFrame,
        QGridLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMainWindow,
        QPushButton,
        QSizePolicy,
        QVBoxLayout,
        QWidget,
    )
except ImportError as error:
    print(f"缺少必要套件：{error}")
    print("請先執行：pip install -r requirements.txt")
    sys.exit(1)


DEFAULT_BROKER_URL = "wss://broker.emqx.io:8084/mqtt"
INVITE_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAX_IMAGE_BYTES = 768 * 1024
IMAGE_QUALITY_STEPS = (86, 78, 70, 62, 54, 46, 38)
IMAGE_SIDE_STEPS = (1600, 1280, 1024, 800, 640, 480, 360)
SUPPORTED_IMAGE_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/bmp",
}


@dataclass
class IncomingMessage:
    topic: str
    received_at: str
    payload: dict


class MqttBridge(QObject):
    status_changed = pyqtSignal(str, str)
    message_received = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self.client = None
        self.invite_code = ""
        self.user = ""
        self.room_ready = False
        self.pending_subscribe_mid = None
        self.broker_url = ""
        self.connect_started_at = 0
        self.watchdog = QTimer(self)
        self.watchdog.setInterval(2500)
        self.watchdog.timeout.connect(self._watch_connection)

    def connect_room(self, broker_url: str, invite_code: str, user: str):
        broker_url = broker_url.strip()
        invite_code = invite_code.strip()
        user = user.strip() or "Guest"

        if not broker_url.startswith(("ws://", "wss://")):
            raise ValueError("Broker URL 必須使用 ws:// 或 wss://。")

        if not INVITE_CODE_RE.match(invite_code):
            raise ValueError("Invite code 只能使用英數字、底線或連字號，長度 1 到 64。")

        self.disconnect_room(emit_status=False)
        self.invite_code = invite_code
        self.user = user
        self.room_ready = False
        self.pending_subscribe_mid = None
        self.broker_url = broker_url
        self._start_client()

    def disconnect_room(self, emit_status: bool = True):
        self.watchdog.stop()
        if self.client:
            old_client = self.client
            self.client = None
            self.room_ready = False
            self.pending_subscribe_mid = None
            old_client.disconnect()
            old_client.loop_stop()

        self.invite_code = ""
        self.broker_url = ""

        if emit_status:
            self.status_changed.emit("disconnected", "已離開聊天室。")

    def send_text(self, content: str):
        content = content.strip()
        if not content:
            return

        self.publish_message(
            {
                "type": "text",
                "user": self.user,
                "content": content,
            }
        )

    def send_image(self, file_path: str):
        path = Path(file_path)
        data = path.read_bytes()
        mime_type = mimetypes.guess_type(path.name)[0]
        if mime_type not in SUPPORTED_IMAGE_MIME_TYPES:
            raise ValueError("僅支援 PNG、JPG、GIF、WebP 或 BMP 圖片。")

        original_size = len(data)
        compressed = False
        if original_size > MAX_IMAGE_BYTES:
            data, mime_type = self._compress_image(path)
            compressed = True

        encoded = base64.b64encode(data).decode("ascii")
        self.publish_message(
            {
                "type": "image",
                "user": self.user,
                "url": f"data:{mime_type};base64,{encoded}",
                "filename": path.name,
                "size": len(data),
                "originalSize": original_size,
                "compressed": compressed,
            }
        )

    def _compress_image(self, path: Path):
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            raise ValueError("圖片太大且無法壓縮，請改選其他圖片。")

        best_data = b""
        for side in IMAGE_SIDE_STEPS:
            scaled = pixmap
            if max(pixmap.width(), pixmap.height()) > side:
                scaled = pixmap.scaled(
                    side,
                    side,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )

            for quality in IMAGE_QUALITY_STEPS:
                data = self._pixmap_to_jpeg_bytes(scaled, quality)
                if data and (not best_data or len(data) < len(best_data)):
                    best_data = data
                if data and len(data) <= MAX_IMAGE_BYTES:
                    return data, "image/jpeg"

        if best_data:
            return best_data, "image/jpeg"

        raise ValueError("圖片壓縮失敗，請改選其他圖片。")

    def _pixmap_to_jpeg_bytes(self, pixmap: QPixmap, quality: int) -> bytes:
        byte_array = QByteArray()
        buffer = QBuffer(byte_array)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        ok = pixmap.save(buffer, "JPG", quality)
        buffer.close()

        if not ok:
            return b""

        return bytes(byte_array)

    def publish_message(self, message: dict):
        if not self.client or not self.client.is_connected() or not self.room_ready:
            raise RuntimeError("聊天室尚未準備好，請等狀態顯示已加入後再傳送。")

        topic = f"chat/{self.invite_code}/msg"
        info = self.client.publish(topic, json.dumps(message, ensure_ascii=False), qos=0, retain=False)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"訊息送出失敗，錯誤碼：{info.rc}")

    def _create_client(self, client_id: str):
        try:
            return mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=client_id,
                clean_session=True,
                transport="websockets",
                protocol=mqtt.MQTTv311,
            )
        except (AttributeError, TypeError):
            return mqtt.Client(
                client_id=client_id,
                clean_session=True,
                transport="websockets",
                protocol=mqtt.MQTTv311,
            )

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if client is not self.client:
            return

        self.room_ready = False
        if not self._connect_success(reason_code):
            self.status_changed.emit("error", f"連線失敗：{reason_code}")
            return

        self.connect_started_at = time.monotonic()
        topic = f"chat/{self.invite_code}/#"
        result, mid = client.subscribe(topic, qos=0)
        if result != mqtt.MQTT_ERR_SUCCESS:
            self.status_changed.emit("error", f"訂閱失敗，錯誤碼：{result}")
            return

        self.pending_subscribe_mid = mid
        self.status_changed.emit("subscribing", f"已連上 broker，正在訂閱 {topic}")

    def _on_subscribe(self, client, userdata, mid, *args):
        if client is not self.client or mid != self.pending_subscribe_mid:
            return

        self.room_ready = True
        self.pending_subscribe_mid = None
        self.status_changed.emit("connected", f"已加入聊天室 {self.invite_code}")

    def _on_connect_fail(self, client, userdata):
        if client is not self.client:
            return

        self.room_ready = False
        self.connect_started_at = time.monotonic()
        self.status_changed.emit("offline", "連線失敗，會繼續重試同一個 broker。")

    def _on_disconnect(self, client, userdata, *args):
        if client is not self.client:
            return

        self.room_ready = False
        self.pending_subscribe_mid = None
        self.connect_started_at = time.monotonic()
        self.status_changed.emit("offline", "連線中斷，正在嘗試重新連線。")

    def _on_message(self, client, userdata, mqtt_message):
        if client is not self.client:
            return

        raw = mqtt_message.payload.decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("payload is not an object")
            if payload.get("type") not in {"text", "image", "file"}:
                raise ValueError("unsupported message type")
        except ValueError:
            payload = {
                "type": "text",
                "user": "system",
                "content": f"收到無法解析的訊息：{raw}",
            }

        self.message_received.emit(
            IncomingMessage(
                topic=mqtt_message.topic,
                received_at=datetime.now().strftime("%H:%M:%S"),
                payload=payload,
            )
        )

    def _connect_success(self, reason_code) -> bool:
        try:
            return int(reason_code) == 0
        except (TypeError, ValueError):
            return str(reason_code).lower() in {"success", "0"}

    def _start_client(self):
        broker_url = self.broker_url
        parsed = urlparse(broker_url)
        host = parsed.hostname
        port = parsed.port or (8884 if parsed.scheme == "wss" else 8000)
        path = parsed.path or "/mqtt"

        if not host:
            self.status_changed.emit("error", "Broker URL 缺少 host。")
            return

        if self.client:
            old_client = self.client
            self.client = None
            old_client.disconnect()
            old_client.loop_stop()

        self.room_ready = False
        self.pending_subscribe_mid = None
        self.connect_started_at = time.monotonic()

        client_id = f"python-qt-chat-{random.randrange(16**8):08x}-{int(time.time())}"
        client = self._create_client(client_id)
        client.ws_set_options(path=path)
        client.reconnect_delay_set(min_delay=1, max_delay=4)
        client.max_inflight_messages_set(20)
        client.max_queued_messages_set(50)

        if parsed.scheme == "wss":
            client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

        client.on_connect = self._on_connect
        client.on_connect_fail = self._on_connect_fail
        client.on_disconnect = self._on_disconnect
        client.on_subscribe = self._on_subscribe
        client.on_message = self._on_message

        self.client = client
        self.status_changed.emit("connecting", f"連線中：{host}:{port}")

        client.connect_async(host, port, keepalive=45)
        client.loop_start()
        self.watchdog.start()

    def _restart_same_broker(self, reason: str):
        if not self.broker_url or not self.invite_code:
            return

        self.status_changed.emit("connecting", f"{reason}，正在重新連同一個 broker。")
        self._start_client()

    def _watch_connection(self):
        if not self.invite_code or not self.broker_url:
            self.watchdog.stop()
            return

        if self.room_ready:
            return

        if time.monotonic() - self.connect_started_at >= 8:
            self._restart_same_broker("連線或訂閱逾時")


class GuideCard(QFrame):
    def __init__(self, number: str, title: str, detail: str):
        super().__init__()
        self.setObjectName("guideCard")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        badge = QLabel(number)
        badge.setObjectName("guideBadge")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(30, 30)

        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        title_label = QLabel(title)
        title_label.setObjectName("guideTitle")
        detail_label = QLabel(detail)
        detail_label.setObjectName("guideDetail")
        detail_label.setWordWrap(True)
        text_box.addWidget(title_label)
        text_box.addWidget(detail_label)

        layout.addWidget(badge)
        layout.addLayout(text_box, 1)


class MessageBubble(QFrame):
    def __init__(
        self,
        user: str,
        time_label: str,
        body: str,
        is_mine: bool,
        kind: str,
        image_src: str = "",
    ):
        super().__init__()
        self.setObjectName("messageBubble")
        self.setProperty("mine", "true" if is_mine else "false")
        self.setProperty("kind", kind)
        self.setMaximumWidth(680)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 9, 12, 10)
        layout.setSpacing(5)

        meta = QLabel(f"{user} · {time_label}")
        meta.setObjectName("messageMeta")

        layout.addWidget(meta)
        if kind == "image" and image_src:
            self._add_image_content(layout, body, image_src)
        else:
            content = self._build_text_label(body, kind)
            layout.addWidget(content)

    def _build_text_label(self, body: str, kind: str) -> QLabel:
        content = QLabel(body)
        content.setObjectName("messageBody")
        content.setWordWrap(True)
        content.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        if kind in {"image", "file"}:
            content.setTextFormat(Qt.TextFormat.RichText)
            content.setOpenExternalLinks(True)

        return content

    def _add_image_content(self, layout: QVBoxLayout, body: str, image_src: str):
        pixmap = self._pixmap_from_data_url(image_src)
        if pixmap and not pixmap.isNull():
            image = QLabel()
            image.setObjectName("messageImage")
            image.setAlignment(Qt.AlignmentFlag.AlignCenter)
            image.setPixmap(
                pixmap.scaled(
                    420,
                    300,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            layout.addWidget(image)

            if body:
                caption = self._build_text_label(body, "text")
                layout.addWidget(caption)
            return

        content = self._build_text_label(body, "image")
        layout.addWidget(content)

    def _pixmap_from_data_url(self, image_src: str):
        if not image_src.startswith("data:image/") or ";base64," not in image_src:
            return None

        try:
            encoded = image_src.split(",", 1)[1]
            data = base64.b64decode(encoded, validate=True)
        except (ValueError, IndexError, binascii.Error):
            return None

        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            return None

        return pixmap


class ChatWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.bridge = MqttBridge()
        self.current_user = "Guest"
        self.empty_hint_item = None

        self.setWindowTitle("Python MQTT Chat Demo")
        self.resize(1040, 760)
        self.setMinimumSize(760, 600)

        self.invite_input = QLineEdit("demo-room")
        self.user_input = QLineEdit("Guest")
        self.broker_input = QLineEdit(DEFAULT_BROKER_URL)
        self.join_button = QPushButton("加入聊天室")
        self.leave_button = QPushButton("離開")
        self.status_label = QLabel("未連線")
        self.topic_label = QLabel("chat/demo-room/#")
        self.message_list = QListWidget()
        self.message_input = QLineEdit()
        self.image_button = QPushButton("圖片")
        self.send_button = QPushButton("傳送")

        self._build_ui()
        self._bind_events()
        self._set_connected(False)
        self.show_empty_hint()

    def _build_ui(self):
        root = QWidget()
        root.setObjectName("root")
        page = QVBoxLayout(root)
        page.setContentsMargins(24, 22, 24, 22)
        page.setSpacing(16)

        hero = QFrame()
        hero.setObjectName("hero")
        hero_layout = QGridLayout(hero)
        hero_layout.setContentsMargins(20, 18, 20, 18)
        hero_layout.setHorizontalSpacing(16)
        hero_layout.setVerticalSpacing(14)

        title = QLabel("MQTT Chat Console")
        title.setObjectName("title")
        subtitle = QLabel("用 invite code 建立臨時聊天室，所有訊息透過 public MQTT broker 即時同步。")
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)

        hero_layout.addWidget(title, 0, 0, 1, 3)
        hero_layout.addWidget(subtitle, 1, 0, 1, 3)

        guide_row = QHBoxLayout()
        guide_row.setSpacing(10)
        guide_row.addWidget(GuideCard("1", "填入房間代碼", "同一組 invite code 就會進同一間聊天室。"))
        guide_row.addWidget(GuideCard("2", "按加入聊天室", "訂閱成功後，下方輸入框會自動啟用。"))
        guide_row.addWidget(GuideCard("3", "開始傳訊息", "按 Enter 或傳送，其他同房間的人會即時看到。"))
        hero_layout.addLayout(guide_row, 2, 0, 1, 3)

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(7)

        form.addWidget(self._field_label("Invite code"), 0, 0)
        form.addWidget(self._field_label("User"), 0, 1)
        form.addWidget(self._field_label("Broker WebSocket URL"), 0, 2)
        form.addWidget(self.invite_input, 1, 0)
        form.addWidget(self.user_input, 1, 1)
        form.addWidget(self.broker_input, 1, 2)
        form.addWidget(self.join_button, 1, 3)
        form.addWidget(self.leave_button, 1, 4)
        form.setColumnStretch(0, 1)
        form.setColumnStretch(1, 1)
        form.setColumnStretch(2, 3)
        hero_layout.addLayout(form, 3, 0, 1, 3)

        chat_panel = QFrame()
        chat_panel.setObjectName("panel")
        chat_layout = QVBoxLayout(chat_panel)
        chat_layout.setContentsMargins(0, 0, 0, 0)
        chat_layout.setSpacing(0)

        header = QWidget()
        header.setObjectName("chatHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(18, 15, 18, 15)
        header_layout.setSpacing(12)

        topic_box = QVBoxLayout()
        topic_box.setSpacing(3)
        topic_label_title = QLabel("正在收聽")
        topic_label_title.setObjectName("smallLabel")
        self.topic_label.setObjectName("topic")
        topic_box.addWidget(topic_label_title)
        topic_box.addWidget(self.topic_label)

        self.status_label.setObjectName("status")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

        header_layout.addLayout(topic_box)
        header_layout.addStretch()
        header_layout.addWidget(self.status_label)

        self.message_list.setObjectName("messageList")
        self.message_list.setSpacing(10)
        self.message_list.setUniformItemSizes(False)

        composer = QWidget()
        composer.setObjectName("composer")
        composer_layout = QHBoxLayout(composer)
        composer_layout.setContentsMargins(14, 14, 14, 14)
        composer_layout.setSpacing(10)
        self.message_input.setPlaceholderText("輸入訊息，按 Enter 傳送")
        composer_layout.addWidget(self.message_input)
        composer_layout.addWidget(self.image_button)
        composer_layout.addWidget(self.send_button)

        chat_layout.addWidget(header)
        chat_layout.addWidget(self.message_list, 1)
        chat_layout.addWidget(composer)

        page.addWidget(hero)
        page.addWidget(chat_panel, 1)
        self.setCentralWidget(root)
        self._apply_style()

    def _field_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        return label

    def _apply_style(self):
        self.setStyleSheet(
            """
            QWidget#root {
                background: #eef2f6;
                color: #172331;
                font-family: "Microsoft JhengHei", "Segoe UI", sans-serif;
                font-size: 14px;
            }
            QFrame#hero {
                background: #ffffff;
                border: 1px solid #d8e0e7;
                border-radius: 8px;
            }
            QFrame#panel {
                background: #ffffff;
                border: 1px solid #d8e0e7;
                border-radius: 8px;
            }
            QWidget#chatHeader, QWidget#composer {
                background: #ffffff;
            }
            QLabel#title {
                color: #102234;
                font-size: 30px;
                font-weight: 850;
            }
            QLabel#subtitle {
                color: #536170;
                font-size: 14px;
            }
            QLabel#fieldLabel, QLabel#smallLabel {
                color: #617080;
                font-size: 12px;
                font-weight: 800;
            }
            QLabel#topic {
                color: #172331;
                font-size: 15px;
                font-weight: 800;
            }
            QFrame#guideCard {
                background: #f6f9fb;
                border: 1px solid #dce5ec;
                border-radius: 8px;
            }
            QLabel#guideBadge {
                color: #ffffff;
                background: #126c66;
                border-radius: 15px;
                font-size: 13px;
                font-weight: 900;
            }
            QLabel#guideTitle {
                color: #172331;
                font-weight: 850;
            }
            QLabel#guideDetail {
                color: #667686;
                font-size: 12px;
            }
            QLabel#status {
                color: white;
                background: #6f7c88;
                border-radius: 14px;
                padding: 7px 12px;
                font-size: 12px;
                font-weight: 850;
            }
            QLineEdit {
                min-height: 36px;
                padding: 5px 11px;
                border: 1px solid #c6d1da;
                border-radius: 7px;
                background: #ffffff;
                color: #172331;
                selection-background-color: #126c66;
            }
            QLineEdit:focus {
                border: 1px solid #126c66;
                background: #fbfdfd;
            }
            QLineEdit:disabled {
                color: #7b8794;
                background: #f4f7f9;
            }
            QPushButton {
                min-height: 38px;
                padding: 5px 16px;
                border: 0;
                border-radius: 7px;
                background: #126c66;
                color: white;
                font-weight: 850;
            }
            QPushButton:hover {
                background: #0d5a55;
            }
            QPushButton:disabled {
                background: #b9c6cf;
                color: #eef3f6;
            }
            QListWidget#messageList {
                border-top: 1px solid #e1e8ee;
                border-bottom: 1px solid #e1e8ee;
                border-left: 0;
                border-right: 0;
                background: #f7f9fb;
                padding: 14px;
                outline: 0;
            }
            QListWidget#messageList::item {
                border: 0;
                background: transparent;
            }
            QFrame#messageBubble {
                border-radius: 8px;
                border: 1px solid #d8e1e8;
                background: #ffffff;
            }
            QFrame#messageBubble[mine="true"] {
                border: 1px solid #126c66;
                background: #e9f5f3;
            }
            QFrame#messageBubble[kind="system"] {
                border: 1px solid #d9e1e8;
                background: #eef3f7;
            }
            QLabel#messageMeta {
                color: #667686;
                font-size: 12px;
                font-weight: 800;
            }
            QLabel#messageBody {
                color: #172331;
                font-size: 14px;
                line-height: 1.45;
            }
            """
        )

    def _bind_events(self):
        self.join_button.clicked.connect(self.join_room)
        self.leave_button.clicked.connect(self.leave_room)
        self.send_button.clicked.connect(self.send_message)
        self.image_button.clicked.connect(self.send_image)
        self.message_input.returnPressed.connect(self.send_message)
        self.invite_input.textChanged.connect(self.update_topic_label)
        self.bridge.status_changed.connect(self.on_status_changed)
        self.bridge.message_received.connect(self.on_message_received)

    def join_room(self):
        self.current_user = self.user_input.text().strip() or "Guest"
        self.message_list.clear()
        self.empty_hint_item = None
        self.update_topic_label()

        try:
            self.bridge.connect_room(
                self.broker_input.text(),
                self.invite_input.text(),
                self.current_user,
            )
        except (ValueError, RuntimeError) as error:
            self.show_system_message(str(error))
            self.show_empty_hint()

    def leave_room(self):
        self.bridge.disconnect_room()
        self.show_empty_hint()

    def send_message(self):
        text = self.message_input.text()
        if not text.strip():
            return

        try:
            self.bridge.send_text(text)
            self.message_input.clear()
        except RuntimeError as error:
            self.show_system_message(str(error))

    def send_image(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "選擇圖片",
            "",
            "圖片 (*.png *.jpg *.jpeg *.gif *.webp *.bmp);;所有檔案 (*)",
        )
        if not file_path:
            return

        try:
            self.bridge.send_image(file_path)
        except (OSError, RuntimeError, ValueError) as error:
            self.show_system_message(str(error))

    def update_topic_label(self):
        invite_code = self.invite_input.text().strip() or "invite_code"
        self.topic_label.setText(f"chat/{invite_code}/#")

    def on_status_changed(self, state: str, detail: str):
        self.status_label.setText(detail)
        color = {
            "connected": "#12715e",
            "connecting": "#a66512",
            "subscribing": "#1b6ea8",
            "offline": "#b63d38",
            "error": "#b63d38",
            "disconnected": "#6f7c88",
        }.get(state, "#6f7c88")
        self.status_label.setStyleSheet(
            f"""
            color: white;
            background: {color};
            border-radius: 14px;
            padding: 7px 12px;
            font-size: 12px;
            font-weight: 850;
            """
        )
        self._set_connection_state(state)

    def on_message_received(self, message: IncomingMessage):
        if self.empty_hint_item:
            self.message_list.takeItem(self.message_list.row(self.empty_hint_item))
            self.empty_hint_item = None

        payload = message.payload
        message_type = payload.get("type")
        raw_user = str(payload.get("user") or "unknown").strip() or "unknown"
        is_mine = raw_user == self.current_user

        if message_type == "image":
            raw_url = str(payload.get("url") or "")
            filename = self._escape_html(payload.get("filename", "圖片"))
            if raw_url.startswith("data:image/"):
                body = filename
            else:
                url = self._escape_html(raw_url)
                body = f'圖片 URL：<a href="{url}">{url}</a>'
        elif message_type == "file":
            filename = self._escape_html(payload.get("filename", "file"))
            url = self._escape_html(payload.get("url", ""))
            body = f'檔案：{filename}<br><a href="{url}">{url}</a>'
        else:
            body = self._escape_html(payload.get("content", ""))

        self.add_bubble(
            raw_user,
            message.received_at,
            body,
            is_mine,
            message_type,
            str(payload.get("url") or "") if message_type == "image" else "",
        )

    def show_empty_hint(self):
        if self.message_list.count() > 0:
            return

        self.empty_hint_item = self.add_bubble(
            "系統",
            datetime.now().strftime("%H:%M:%S"),
            "先確認 invite code，按「加入聊天室」。同一組 invite code 的人會出現在同一個房間。",
            False,
            "system",
        )

    def show_system_message(self, content: str):
        self.add_bubble(
            "系統",
            datetime.now().strftime("%H:%M:%S"),
            self._escape_html(content),
            False,
            "system",
        )

    def add_bubble(
        self,
        user: str,
        time_label: str,
        body: str,
        is_mine: bool,
        kind: str,
        image_src: str = "",
    ):
        bubble = MessageBubble(user, time_label, body, is_mine, kind, image_src)
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        if is_mine:
            row.addStretch()
            row.addWidget(bubble)
        else:
            row.addWidget(bubble)
            row.addStretch()

        item = QListWidgetItem()
        item.setSizeHint(QSize(100, bubble.sizeHint().height() + 10))
        self.message_list.addItem(item)
        self.message_list.setItemWidget(item, container)
        self.message_list.scrollToBottom()
        return item

    def _set_connected(self, connected: bool):
        self.message_input.setEnabled(connected)
        self.image_button.setEnabled(connected)
        self.send_button.setEnabled(connected)
        self.leave_button.setEnabled(connected)
        self.join_button.setEnabled(not connected)
        self.invite_input.setEnabled(not connected)
        self.user_input.setEnabled(not connected)
        self.broker_input.setEnabled(not connected)

        if connected:
            self.message_input.setFocus()

    def _set_connection_state(self, state: str):
        connected = state == "connected"
        actively_trying = state in {"connecting", "subscribing", "offline", "error"}

        self.message_input.setEnabled(connected)
        self.image_button.setEnabled(connected)
        self.send_button.setEnabled(connected)
        self.leave_button.setEnabled(connected or actively_trying)
        self.join_button.setEnabled(not connected and not actively_trying)
        self.invite_input.setEnabled(not connected and not actively_trying)
        self.user_input.setEnabled(not connected and not actively_trying)
        self.broker_input.setEnabled(not connected and not actively_trying)

        if connected:
            self.message_input.setFocus()

    def _escape_html(self, value) -> str:
        return (
            str(value)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    def closeEvent(self, event):
        self.bridge.disconnect_room(emit_status=False)
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = ChatWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
