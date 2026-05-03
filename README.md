# Python MQTT Chat Demo

這是一個不用後端的 Python 桌面聊天室 demo。

技術：

- Python
- Qt6 UI：`PyQt6`
- MQTT：`paho-mqtt`
- 透過 WebSocket 連 public broker

## 功能

- 使用 `invite_code` 作為聊天室 ID
- 訂閱 `chat/{invite_code}/#`
- 發送文字訊息到 `chat/{invite_code}/msg`
- 支援 JSON 訊息格式：
  - `{"type":"text","user":"Alice","content":"Hello"}`
  - `{"type":"image","user":"Alice","url":"https://example.com/image.jpg"}`
  - `{"type":"file","user":"Alice","filename":"demo.pdf","url":"https://example.com/demo.pdf"}`
- 圖片與檔案只傳 URL，不直接傳內容
- Enter 可發送訊息
- 即時顯示收到的 MQTT 訊息

## 介面引導

App 開啟後會顯示三個使用步驟：

1. 填入房間代碼
2. 按「加入聊天室」
3. 開始傳訊息

同一組 `invite_code` 的使用者會進入同一間聊天室。

## 執行

```bash
pip install -r requirements.txt
python app.py
```

預設 broker：

```text
wss://broker.hivemq.com:8884/mqtt
```

也可改成：

```text
ws://broker.hivemq.com:8000/mqtt
ws://broker.mqttdashboard.com:8000/mqtt
```

## Topic

同一個 `invite_code` 的使用者會在同一間聊天室：

```text
subscribe: chat/{invite_code}/#
publish:   chat/{invite_code}/msg
```

例如 invite code 是 `demo-room`：

```text
subscribe: chat/demo-room/#
publish:   chat/demo-room/msg
```

## 限制

- 不使用後端
- 不做帳號系統
- 不儲存歷史訊息
- Public broker 只適合 demo，不適合正式環境

## 檔案

- `app.py`：Python Qt6 聊天室主程式
- `requirements.txt`：需要安裝的 Python 套件
