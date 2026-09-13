const dialog = document.querySelector('#settings-dialog');
const form = document.querySelector('#settings-form');
const statusText = document.querySelector('#form-status');
const message = document.querySelector('#message');
const messageText = document.querySelector('#message-text');
const caption = document.querySelector('#caption');
const photos = [document.querySelector('#photo-a'), document.querySelector('#photo-b')];
const video = document.querySelector('#video');
const collage = document.querySelector('#collage');
let active = 0;
let currentPath = '';
let timer;
let paused = false;
let commandSequence = null;
let loading = false;
let displayMode = '';
let collageItems = [];

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
    await activateMode(displayMode || 'photos', true);
  } catch (error) {
    statusText.textContent = error.message;
    statusText.className = 'error';
  }
}

function hideSlideshow() {
  photos.forEach(photo => photo.classList.remove('active'));
  video.pause();
  video.classList.remove('active');
}

function hideCollage() {
  collage.classList.remove('active');
  collage.replaceChildren();
  collageItems = [];
}

async function reportCurrent(item) {
  currentPath = item.path;
  caption.textContent = item.name;
  await json('/api/current', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({path: item.path, name: item.name, kind: item.kind}),
  });
}

async function showNext() {
  if (loading || displayMode !== 'photos') return;
  loading = true;
  let nextDelay = 0;
  clearTimeout(timer);
  try {
    const item = await json(`/api/next?after=${encodeURIComponent(currentPath)}`);
    if (item.kind === 'video') {
      photos.forEach(photo => photo.classList.remove('active'));
      video.muted = false;
      video.volume = 1;
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
      nextDelay = item.interval_seconds * 1000;
    }
    await reportCurrent(item);
    message.classList.add('hidden');
  } catch (error) {
    messageText.textContent = error.message;
    message.classList.remove('hidden');
    nextDelay = 15000;
  } finally {
    loading = false;
    if (nextDelay && !paused && displayMode === 'photos') timer = setTimeout(showNext, nextDelay);
  }
}

function randomTransform() {
  const angle = (Math.random() * 8 - 4).toFixed(2);
  const x = (Math.random() * 1.4 - .7).toFixed(2);
  const y = (Math.random() * 1.4 - .7).toFixed(2);
  return `translate(${x}vw, ${y}vh) rotate(${angle}deg) scale(.96)`;
}

async function setCollageTile(tile, item, transitionSeconds) {
  tile.classList.remove('loaded');
  tile.style.transitionDuration = `${transitionSeconds}s`;
  tile.style.transform = randomTransform();
  await new Promise((resolve, reject) => {
    tile.onload = resolve;
    tile.onerror = () => reject(new Error('A collage photo could not be displayed'));
    tile.src = item.url;
  });
  tile.classList.add('loaded');
}

async function showCollage() {
  if (loading || displayMode !== 'collage') return;
  loading = true;
  let nextDelay = 0;
  clearTimeout(timer);
  try {
    const result = await json('/api/collage');
    hideSlideshow();
    collage.replaceChildren();
    collage.classList.add('active');
    collageItems = result.items;
    const tiles = result.items.map(() => {
      const tile = document.createElement('img');
      tile.className = 'collage-photo';
      tile.alt = '';
      collage.appendChild(tile);
      return tile;
    });
    await Promise.all(tiles.map((tile, index) => setCollageTile(tile, result.items[index], result.transition_seconds)));
    await reportCurrent(result.items[0]);
    message.classList.add('hidden');
    nextDelay = result.interval_seconds * 1000;
  } catch (error) {
    messageText.textContent = error.message;
    message.classList.remove('hidden');
    nextDelay = 15000;
  } finally {
    loading = false;
    if (nextDelay && !paused && displayMode === 'collage') {
      timer = setTimeout(collageItems.length ? rotateCollageItem : showCollage, nextDelay);
    }
  }
}

async function rotateCollageItem() {
  if (loading || paused || displayMode !== 'collage' || !collageItems.length) return;
  loading = true;
  let nextDelay = 0;
  clearTimeout(timer);
  try {
    const index = Math.floor(Math.random() * collageItems.length);
    const previous = collageItems[index];
    const item = await json(`/api/next?kind=image&after=${encodeURIComponent(previous.path)}`);
    const tile = collage.children[index];
    await setCollageTile(tile, item, item.transition_seconds);
    collageItems[index] = item;
    await reportCurrent(item);
    nextDelay = item.interval_seconds * 1000;
  } catch (_error) {
    nextDelay = 15000;
  } finally {
    loading = false;
    if (nextDelay && !paused && displayMode === 'collage') {
      timer = setTimeout(rotateCollageItem, nextDelay);
    }
  }
}

async function activateMode(mode, force = false) {
  if (!force && displayMode === mode) return;
  clearTimeout(timer);
  displayMode = mode;
  if (mode === 'collage') {
    hideCollage();
    await showCollage();
  } else if (mode === 'photos') {
    hideCollage();
    currentPath = '';
    await showNext();
  }
}

async function syncRuntime() {
  try {
    const state = await json('/api/runtime');
    if (state.display_mode !== displayMode) await activateMode(state.display_mode);
    if (commandSequence !== null && state.command_sequence !== commandSequence) {
      if (displayMode === 'collage') await rotateCollageItem();
      else await showNext();
    }
    commandSequence = state.command_sequence;
    if (state.paused !== paused) {
      paused = state.paused;
      if (paused) {
        clearTimeout(timer);
        video.pause();
      } else if (displayMode === 'collage') {
        rotateCollageItem();
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

async function start() {
  try {
    const state = await json('/api/runtime');
    paused = state.paused;
    commandSequence = state.command_sequence;
    await activateMode(state.display_mode || 'photos');
  } catch (_error) {
    await activateMode('photos');
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
