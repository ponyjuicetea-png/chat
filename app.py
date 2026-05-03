import json
import random
import re
import ssl
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

try:
    import paho.mqtt.client as mqtt
    from PyQt6.QtCore import QObject, QSize, Qt, pyqtSignal
    from PyQt6.QtWidgets import (
        QApplication,
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


DEFAULT_BROKER_URL = "wss://broker.hivemq.com:8884/mqtt"
INVITE_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


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

        parsed = urlparse(broker_url)
        host = parsed.hostname
        port = parsed.port or (8884 if parsed.scheme == "wss" else 8000)
        path = parsed.path or "/mqtt"

        if not host:
            raise ValueError("Broker URL 缺少 host。")

        client_id = f"python-qt-chat-{random.randrange(16**8):08x}-{int(time.time())}"
        client = self._create_client(client_id)
        client.ws_set_options(path=path)

        if parsed.scheme == "wss":
            client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message

        self.client = client
        self.status_changed.emit("connecting", f"連線中：{broker_url}")

        client.connect_async(host, port, keepalive=30)
        client.loop_start()

    def disconnect_room(self, emit_status: bool = True):
        if self.client:
            old_client = self.client
            self.client = None
            old_client.loop_stop()
            old_client.disconnect()

        self.invite_code = ""

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

    def publish_message(self, message: dict):
        if not self.client or not self.client.is_connected() or not self.invite_code:
            raise RuntimeError("尚未連上聊天室。")

        topic = f"chat/{self.invite_code}/msg"
        self.client.publish(topic, json.dumps(message, ensure_ascii=False), qos=0, retain=False)

    def _create_client(self, client_id: str):
        try:
            return mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=client_id,
                transport="websockets",
            )
        except (AttributeError, TypeError):
            return mqtt.Client(client_id=client_id, transport="websockets")

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if client is not self.client:
            return

        if not self._connect_success(reason_code):
            self.status_changed.emit("error", f"連線失敗：{reason_code}")
            return

        topic = f"chat/{self.invite_code}/#"
        client.subscribe(topic, qos=0)
        self.status_changed.emit("connected", f"已加入聊天室 {self.invite_code}")

    def _on_disconnect(self, client, userdata, *args):
        if client is not self.client:
            return

        self.status_changed.emit("offline", "連線已中斷，MQTT 會嘗試重連。")

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
    def __init__(self, user: str, time_label: str, body: str, is_mine: bool, kind: str):
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

        content = QLabel(body)
        content.setObjectName("messageBody")
        content.setWordWrap(True)
        content.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        if kind in {"image", "file"}:
            content.setTextFormat(Qt.TextFormat.RichText)
            content.setOpenExternalLinks(True)

        layout.addWidget(meta)
        layout.addWidget(content)


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
        guide_row.addWidget(GuideCard("2", "按加入聊天室", "連線成功後，下方輸入框會自動啟用。"))
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

    def update_topic_label(self):
        invite_code = self.invite_input.text().strip() or "invite_code"
        self.topic_label.setText(f"chat/{invite_code}/#")

    def on_status_changed(self, state: str, detail: str):
        self.status_label.setText(detail)
        color = {
            "connected": "#12715e",
            "connecting": "#a66512",
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
        self._set_connected(state == "connected")

    def on_message_received(self, message: IncomingMessage):
        if self.empty_hint_item:
            self.message_list.takeItem(self.message_list.row(self.empty_hint_item))
            self.empty_hint_item = None

        payload = message.payload
        message_type = payload.get("type")
        user = payload.get("user", "unknown")

        if message_type == "image":
            url = self._escape_html(payload.get("url", ""))
            body = f'圖片 URL：<a href="{url}">{url}</a>'
        elif message_type == "file":
            filename = self._escape_html(payload.get("filename", "file"))
            url = self._escape_html(payload.get("url", ""))
            body = f'檔案：{filename}<br><a href="{url}">{url}</a>'
        else:
            body = self._escape_html(payload.get("content", ""))

        display_user = "你" if user == self.current_user else user
        self.add_bubble(display_user, message.received_at, body, user == self.current_user, message_type)

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

    def add_bubble(self, user: str, time_label: str, body: str, is_mine: bool, kind: str):
        bubble = MessageBubble(user, time_label, body, is_mine, kind)
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
        self.send_button.setEnabled(connected)
        self.leave_button.setEnabled(connected)
        self.join_button.setEnabled(not connected)
        self.invite_input.setEnabled(not connected)
        self.user_input.setEnabled(not connected)
        self.broker_input.setEnabled(not connected)

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
