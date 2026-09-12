const dialog = document.querySelector('#settings-dialog');
const form = document.querySelector('#settings-form');
const statusText = document.querySelector('#form-status');
const message = document.querySelector('#message');
const messageText = document.querySelector('#message-text');
const caption = document.querySelector('#caption');
const photos = [document.querySelector('#photo-a'), document.querySelector('#photo-b')];
const video = document.querySelector('#video');
let active = 0;
let currentPath = '';
let timer;
let paused = false;
let commandSequence = null;
let loading = false;

async function json(url, options) {
  const response = await fetch(url, options);
  const body = await response.json();
  if (!response.ok || body.ok === false) throw new Error(body.error || `Request failed (${response.status})`);
  return body;
}

function field(name) { return form.elements.namedItem(name); }
function toggleSource() {
  const smb = field('source_type').value === 'smb';
  document.querySelector('#smb-fields').hidden = !smb;
  document.querySelector('#local-fields').hidden = smb;
}

async function loadSettings() {
  const config = await json('/api/config');
  for (const [key, value] of Object.entries(config)) {
    if (field(key) && key !== 'smb_password') field(key).value = value;
  }
  toggleSource();
}

async function saveSettings(test) {
  statusText.className = '';
  statusText.textContent = 'Saving…';
  const payload = Object.fromEntries(new FormData(form).entries());
  try {
    await json('/api/config', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
    if (test) {
      const result = await json('/api/refresh', {method: 'POST'});
      statusText.textContent = `Connected — found ${result.count} photos and videos.`;
    } else {
      statusText.textContent = 'Settings saved.';
    }
    statusText.className = 'success';
    field('smb_password').value = '';
    start();
  } catch (error) {
    statusText.textContent = error.message;
    statusText.className = 'error';
  }
}

async function showNext() {
  if (loading) return;
  loading = true;
  clearTimeout(timer);
  try {
    const item = await json(`/api/next?after=${encodeURIComponent(currentPath)}`);
    if (item.kind === 'video') {
      photos.forEach(photo => photo.classList.remove('active'));
      video.style.objectFit = item.fit_mode;
      video.style.transitionDuration = `${item.transition_seconds}s`;
      video.src = item.url;
      video.classList.add('active');
      video.onended = showNext;
      video.onerror = () => setTimeout(showNext, 3000);
      if (!paused) await video.play();
    } else {
      video.pause();
      video.removeAttribute('src');
      video.load();
      video.classList.remove('active');
      const incoming = photos[1 - active];
      incoming.style.objectFit = item.fit_mode;
      incoming.style.transitionDuration = `${item.transition_seconds}s`;
      await new Promise((resolve, reject) => {
        incoming.onload = resolve;
        incoming.onerror = () => reject(new Error('The selected image could not be displayed'));
        incoming.src = item.url;
      });
      photos[active].classList.remove('active');
      incoming.classList.add('active');
      active = 1 - active;
      if (!paused) timer = setTimeout(showNext, item.interval_seconds * 1000);
    }
    currentPath = item.path;
    caption.textContent = item.name;
    message.classList.add('hidden');
    await json('/api/current', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({path: item.path, name: item.name, kind: item.kind}),
    });
  } catch (error) {
    messageText.textContent = error.message;
    message.classList.remove('hidden');
    timer = setTimeout(showNext, 15000);
  } finally {
    loading = false;
  }
}

async function start() { currentPath = ''; await showNext(); }

async function syncRuntime() {
  try {
    const state = await json('/api/runtime');
    if (commandSequence !== null && state.command_sequence !== commandSequence) showNext();
    commandSequence = state.command_sequence;
    if (state.paused !== paused) {
      paused = state.paused;
      if (paused) {
        clearTimeout(timer);
        video.pause();
      } else if (video.classList.contains('active')) {
        video.play();
      } else {
        showNext();
      }
    }
  } catch (_error) {
    // A temporary network interruption should not replace the current picture.
  }
}
document.querySelector('#settings-button').addEventListener('click', async () => { await loadSettings(); dialog.showModal(); });
document.querySelector('#open-settings-primary').addEventListener('click', async () => { await loadSettings(); dialog.showModal(); });
document.querySelector('#close-settings').addEventListener('click', () => dialog.close());
field('source_type').addEventListener('change', toggleSource);
form.addEventListener('submit', async event => { event.preventDefault(); await saveSettings(false); });
document.querySelector('#test-button').addEventListener('click', () => saveSettings(true));
start();
setInterval(syncRuntime, 750);
