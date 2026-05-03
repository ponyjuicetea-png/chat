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
const sendButton = document.querySelector('#sendButton');

let connected = false;
let currentUser = userNameInput.value;

function setConnectedState(isConnected) {
  connected = isConnected;
  messageInput.disabled = !isConnected;
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
    const link = document.createElement('a');
    link.href = message.url;
    link.target = '_blank';
    link.rel = 'noreferrer';

    const image = document.createElement('img');
    image.src = message.url;
    image.alt = message.content || 'image';
    link.append(image);
    body.append(link);
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

window.chatMqtt.onStatus((status) => {
  setStatus(status);
});

window.chatMqtt.onMessage((message) => {
  appendMessage(message);
});

setConnectedState(false);
