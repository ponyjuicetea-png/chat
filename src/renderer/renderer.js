const joinForm = document.querySelector('#joinForm');
const inviteCodeInput = document.querySelector('#inviteCode');
const userNameInput = document.querySelector('#userName');
const brokerUrlInput = document.querySelector('#brokerUrl');
const joinButton = document.querySelector('#joinButton');
const leaveButton = document.querySelector('#leaveButton');
const topicLabel = document.querySelector('#topicLabel');
const statusBadge = document.querySelector('#statusBadge');
const messageList = document.querySelector('#messageList');
const messageForm = document.querySelector('#messageForm');
const messageInput = document.querySelector('#messageInput');
const imageButton = document.querySelector('#imageButton');
const imageInput = document.querySelector('#imageInput');
const sendButton = document.querySelector('#sendButton');

const MAX_IMAGE_BYTES = 768 * 1024;
const IMAGE_SIDE_STEPS = [1600, 1280, 1024, 800, 640, 480, 360];
const IMAGE_QUALITY_STEPS = [0.86, 0.78, 0.7, 0.62, 0.54, 0.46, 0.38];
const SUPPORTED_IMAGE_TYPES = new Set([
  'image/png',
  'image/jpeg',
  'image/gif',
  'image/webp',
  'image/bmp'
]);
const IMAGE_TYPE_BY_EXTENSION = new Map([
  ['.png', 'image/png'],
  ['.jpg', 'image/jpeg'],
  ['.jpeg', 'image/jpeg'],
  ['.gif', 'image/gif'],
  ['.webp', 'image/webp'],
  ['.bmp', 'image/bmp']
]);

let connected = false;
let currentUser = userNameInput.value;

function setConnectedState(isConnected) {
  connected = isConnected;
  messageInput.disabled = !isConnected;
  imageButton.disabled = !isConnected;
  sendButton.disabled = !isConnected;
  leaveButton.disabled = !isConnected;
  joinButton.disabled = isConnected;
  inviteCodeInput.disabled = isConnected;
  userNameInput.disabled = isConnected;
  brokerUrlInput.disabled = isConnected;

  if (isConnected) {
    messageInput.focus();
  }
}

function setStatus({ state, detail }) {
  statusBadge.className = `status ${state}`;
  statusBadge.textContent = detail || state;
  setConnectedState(state === 'connected');
}

function formatTime(isoString) {
  return new Intl.DateTimeFormat('zh-TW', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  }).format(new Date(isoString));
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener('load', () => resolve(String(reader.result || '')));
    reader.addEventListener('error', () => reject(reader.error || new Error('圖片讀取失敗。')));
    reader.readAsDataURL(file);
  });
}

function canvasToBlob(canvas, type, quality) {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => {
        if (blob) {
          resolve(blob);
        } else {
          reject(new Error('圖片壓縮失敗。'));
        }
      },
      type,
      quality
    );
  });
}

function loadImage(file) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    const url = URL.createObjectURL(file);

    image.addEventListener(
      'load',
      () => {
        URL.revokeObjectURL(url);
        resolve(image);
      },
      { once: true }
    );
    image.addEventListener(
      'error',
      () => {
        URL.revokeObjectURL(url);
        reject(new Error('圖片讀取失敗，無法自動壓縮。'));
      },
      { once: true }
    );

    image.src = url;
  });
}

function getImageType(file) {
  if (SUPPORTED_IMAGE_TYPES.has(file.type)) {
    return file.type;
  }

  const lowerName = file.name.toLowerCase();
  for (const [extension, type] of IMAGE_TYPE_BY_EXTENSION) {
    if (lowerName.endsWith(extension)) {
      return type;
    }
  }

  return '';
}

function getScaledSize(width, height, maxSide) {
  const largestSide = Math.max(width, height);
  if (largestSide <= maxSide) {
    return { width, height };
  }

  const ratio = maxSide / largestSide;
  return {
    width: Math.max(1, Math.round(width * ratio)),
    height: Math.max(1, Math.round(height * ratio))
  };
}

function isDisplayableImageSource(source) {
  return (
    source.startsWith('data:image/') ||
    source.startsWith('https://') ||
    source.startsWith('http://')
  );
}

async function compressImageFile(file) {
  const image = await loadImage(file);
  const canvas = document.createElement('canvas');
  const context = canvas.getContext('2d');
  let bestBlob = null;

  if (!context) {
    throw new Error('圖片壓縮失敗。');
  }

  for (const side of IMAGE_SIDE_STEPS) {
    const size = getScaledSize(image.naturalWidth, image.naturalHeight, side);
    canvas.width = size.width;
    canvas.height = size.height;
    context.fillStyle = '#ffffff';
    context.fillRect(0, 0, size.width, size.height);
    context.drawImage(image, 0, 0, size.width, size.height);

    for (const quality of IMAGE_QUALITY_STEPS) {
      const blob = await canvasToBlob(canvas, 'image/jpeg', quality);
      if (!bestBlob || blob.size < bestBlob.size) {
        bestBlob = blob;
      }

      if (blob.size <= MAX_IMAGE_BYTES) {
        return {
          url: await readFileAsDataUrl(blob),
          size: blob.size,
          compressed: true
        };
      }
    }
  }

  if (bestBlob) {
    return {
      url: await readFileAsDataUrl(bestBlob),
      size: bestBlob.size,
      compressed: true
    };
  }

  throw new Error('圖片壓縮失敗，請改選其他圖片。');
}

async function sendImageFile(file) {
  const imageType = getImageType(file);
  if (!imageType) {
    appendSystemMessage('僅支援 PNG、JPG、GIF、WebP 或 BMP 圖片。');
    return;
  }

  imageButton.disabled = true;

  try {
    let upload;

    if (file.size > MAX_IMAGE_BYTES) {
      upload = await compressImageFile(file);
    } else {
      upload = {
        url: await readFileAsDataUrl(file),
        size: file.size,
        compressed: false
      };

      if (!upload.url.startsWith('data:image/')) {
        upload.url = upload.url.replace(/^data:[^;]*;base64,/, `data:${imageType};base64,`);
      }
    }

    window.chatMqtt.sendImage({
      url: upload.url,
      filename: file.name,
      size: upload.size,
      originalSize: file.size,
      compressed: upload.compressed
    });
  } catch (error) {
    appendSystemMessage(error.message);
  } finally {
    imageButton.disabled = !connected;
  }
}

function appendMessage(event) {
  const message = event.payload;
  const item = document.createElement('article');
  const isMine = message.user === currentUser;
  item.className = `message ${isMine ? 'mine' : 'theirs'} ${message.type}`;

  const meta = document.createElement('div');
  meta.className = 'message-meta';
  meta.textContent = `${message.user || 'unknown'} · ${formatTime(event.receivedAt)}`;

  const body = document.createElement('div');
  body.className = 'message-body';

  if (message.type === 'image') {
    const source = String(message.url || '');
    if (source && isDisplayableImageSource(source)) {
      const link = document.createElement('a');
      link.href = source;
      link.target = '_blank';
      link.rel = 'noreferrer';

      const image = document.createElement('img');
      image.src = source;
      image.alt = message.filename || message.content || 'image';
      link.append(image);
      body.append(link);

      if (message.filename) {
        const caption = document.createElement('div');
        caption.className = 'image-caption';
        caption.textContent = message.filename;
        body.append(caption);
      }
    } else {
      body.textContent = '圖片無法顯示。';
    }
  } else if (message.type === 'file') {
    const link = document.createElement('a');
    link.href = message.url;
    link.target = '_blank';
    link.rel = 'noreferrer';
    link.textContent = message.filename || message.url;
    body.append(link);
  } else {
    body.textContent = message.content || '';
  }

  item.append(meta, body);
  messageList.append(item);
  messageList.scrollTop = messageList.scrollHeight;
}

function appendSystemMessage(content) {
  appendMessage({
    receivedAt: new Date().toISOString(),
    payload: {
      type: 'text',
      user: 'system',
      content
    }
  });
}

joinForm.addEventListener('submit', (event) => {
  event.preventDefault();

  const inviteCode = inviteCodeInput.value.trim();
  const brokerUrl = brokerUrlInput.value.trim();
  currentUser = userNameInput.value.trim() || 'Guest';
  topicLabel.textContent = `chat/${inviteCode}/#`;
  messageList.textContent = '';

  try {
    window.chatMqtt.connect({
      brokerUrl,
      inviteCode,
      user: currentUser
    });
  } catch (error) {
    appendSystemMessage(error.message);
  }
});

leaveButton.addEventListener('click', () => {
  window.chatMqtt.disconnect();
});

messageForm.addEventListener('submit', (event) => {
  event.preventDefault();

  const text = messageInput.value;
  if (!text.trim()) {
    return;
  }

  try {
    window.chatMqtt.sendText(text);
    messageInput.value = '';
  } catch (error) {
    appendSystemMessage(error.message);
  }
});

imageButton.addEventListener('click', () => {
  if (!connected) {
    return;
  }

  imageInput.click();
});

imageInput.addEventListener('change', () => {
  const [file] = imageInput.files;
  imageInput.value = '';

  if (file) {
    sendImageFile(file);
  }
});

window.chatMqtt.onStatus((status) => {
  setStatus(status);
});

window.chatMqtt.onMessage((message) => {
  appendMessage(message);
});

setConnectedState(false);
