const { contextBridge } = require('electron');
const mqtt = require('mqtt');

let client = null;
let joinedRoom = null;
let displayName = null;

const listeners = {
  status: new Set(),
  message: new Set()
};

function emit(type, payload) {
  for (const callback of listeners[type]) {
    callback(payload);
  }
}

function isValidInviteCode(inviteCode) {
  return /^[A-Za-z0-9_-]{1,64}$/.test(inviteCode);
}

function safeParseMessage(topic, payload) {
  const raw = payload.toString();

  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') {
      throw new Error('Payload is not an object.');
    }

    if (!['text', 'image', 'file'].includes(parsed.type)) {
      throw new Error('Unsupported message type.');
    }

    return {
      topic,
      receivedAt: new Date().toISOString(),
      payload: parsed
    };
  } catch (error) {
    return {
      topic,
      receivedAt: new Date().toISOString(),
      payload: {
        type: 'text',
        user: 'system',
        content: `收到無法解析的訊息：${raw}`
      }
    };
  }
}

function closeClient() {
  if (client) {
    client.end(true);
    client = null;
  }

  joinedRoom = null;
}

function ensureCanPublish() {
  if (!client || !client.connected || !joinedRoom) {
    throw new Error('尚未連上聊天室。');
  }
}

contextBridge.exposeInMainWorld('chatMqtt', {
  connect({ brokerUrl, inviteCode, user }) {
    const room = String(inviteCode || '').trim();
    const name = String(user || '').trim() || 'Guest';
    const url = String(brokerUrl || '').trim();

    if (!url.startsWith('ws://') && !url.startsWith('wss://')) {
      throw new Error('Broker URL 必須使用 ws:// 或 wss://。');
    }

    if (!isValidInviteCode(room)) {
      throw new Error('invite_code 只能使用英數字、底線或連字號，長度 1-64。');
    }

    closeClient();

    joinedRoom = room;
    displayName = name;

    const clientId = `electron-chat-${Math.random().toString(16).slice(2)}-${Date.now()}`;
    const topic = `chat/${joinedRoom}/#`;

    emit('status', {
      state: 'connecting',
      detail: `連線中：${url}`
    });

    const mqttClient = mqtt.connect(url, {
      clientId,
      clean: true,
      connectTimeout: 8000,
      keepalive: 30,
      reconnectPeriod: 2000
    });

    client = mqttClient;

    mqttClient.on('connect', () => {
      if (client !== mqttClient) {
        return;
      }

      mqttClient.subscribe(topic, { qos: 0 }, (error) => {
        if (error) {
          emit('status', {
            state: 'error',
            detail: `訂閱失敗：${error.message}`
          });
          return;
        }

        emit('status', {
          state: 'connected',
          detail: `已加入聊天室 ${joinedRoom}`
        });
      });
    });

    mqttClient.on('reconnect', () => {
      if (client !== mqttClient) {
        return;
      }

      emit('status', {
        state: 'connecting',
        detail: '重新連線中...'
      });
    });

    mqttClient.on('offline', () => {
      if (client !== mqttClient) {
        return;
      }

      emit('status', {
        state: 'offline',
        detail: '目前離線，等待重新連線。'
      });
    });

    mqttClient.on('error', (error) => {
      if (client !== mqttClient) {
        return;
      }

      emit('status', {
        state: 'error',
        detail: error.message
      });
    });

    mqttClient.on('close', () => {
      if (client !== mqttClient) {
        return;
      }

      emit('status', {
        state: 'offline',
        detail: '連線已關閉，準備重連。'
      });
    });

    mqttClient.on('message', (topicName, payload) => {
      if (client !== mqttClient) {
        return;
      }

      emit('message', safeParseMessage(topicName, payload));
    });
  },

  disconnect() {
    closeClient();
    emit('status', {
      state: 'disconnected',
      detail: '已離開聊天室。'
    });
  },

  sendText(content) {
    const text = String(content || '').trim();
    ensureCanPublish();

    if (!text) {
      return;
    }

    const message = {
      type: 'text',
      user: displayName,
      content: text
    };

    client.publish(`chat/${joinedRoom}/msg`, JSON.stringify(message), { qos: 0, retain: false });
  },

  sendImage({ url, filename, size, originalSize, compressed }) {
    const imageUrl = String(url || '');
    ensureCanPublish();

    if (!imageUrl.startsWith('data:image/') || !imageUrl.includes(';base64,')) {
      throw new Error('圖片格式不正確。');
    }

    const message = {
      type: 'image',
      user: displayName,
      url: imageUrl,
      filename: String(filename || 'image'),
      size: Number(size || 0),
      originalSize: Number(originalSize || size || 0),
      compressed: Boolean(compressed)
    };

    client.publish(`chat/${joinedRoom}/msg`, JSON.stringify(message), { qos: 0, retain: false });
  },

  publishMessage(message) {
    ensureCanPublish();

    client.publish(`chat/${joinedRoom}/msg`, JSON.stringify(message), { qos: 0, retain: false });
  },

  onStatus(callback) {
    listeners.status.add(callback);
    return () => listeners.status.delete(callback);
  },

  onMessage(callback) {
    listeners.message.add(callback);
    return () => listeners.message.delete(callback);
  }
});
