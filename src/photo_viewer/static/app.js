const dialog = document.querySelector('#settings-dialog');
const form = document.querySelector('#settings-form');
const statusText = document.querySelector('#form-status');
const message = document.querySelector('#message');
const messageText = document.querySelector('#message-text');
const caption = document.querySelector('#caption');
const captureDate = document.querySelector('#capture-date');
const captureLocation = document.querySelector('#capture-location');
const photos = [document.querySelector('#photo-a'), document.querySelector('#photo-b')];
const video = document.querySelector('#video');
const collage = document.querySelector('#collage');
const visualizer = document.querySelector('#visualizer');
const visualizerContext = visualizer.getContext('2d');
let active = 0;
let currentPath = '';
let timer;
let paused = false;
let commandSequence = null;
let loading = false;
let displayMode = '';
let collageItems = [];
let currentRotation = 0;
let visualizerFrame = 0;
let visualizerPoll = 0;
let visualizerData = {available: false, bass: 0, mid: 0, treble: 0, volume: 0, beat: 0, style: 'kaleidoscope', sensitivity: 100};
const stars = Array.from({length: 220}, () => ({angle: Math.random() * Math.PI * 2, radius: Math.random(), size: .4 + Math.random() * 2.2, speed: .3 + Math.random() * 1.8}));

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
  captureDate.textContent = item.display_date || '';
  captureLocation.textContent = item.display_location || '';
  caption.classList.toggle('visible', displayMode === 'photos' && Boolean(item.display_date || item.display_location));
  await json('/api/current', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({path: item.path, name: item.name, kind: item.kind}),
  });
}

function applyRotation(element, rotation) {
  const degrees = Number(rotation || 0) % 360;
  const quarterTurn = Math.abs(degrees) % 180 === 90;
  const ratio = window.innerWidth / window.innerHeight;
  const scale = quarterTurn ? Math.min(ratio, 1 / ratio) : 1;
  element.style.transform = `rotate(${degrees}deg) scale(${scale})`;
}

async function showNext(direction = 'next') {
  if (loading || displayMode !== 'photos') return;
  loading = true;
  let nextDelay = 0;
  clearTimeout(timer);
  try {
    const item = await json(`/api/next?direction=${encodeURIComponent(direction)}`);
    if (item.kind === 'video') {
      photos.forEach(photo => photo.classList.remove('active'));
      video.muted = false;
      video.volume = 1;
      video.style.objectFit = item.fit_mode;
      video.style.transitionDuration = `${item.transition_seconds}s`;
      video.src = item.url;
      video.classList.add('active');
      video.onended = () => showNext();
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
      applyRotation(incoming, item.rotation);
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
    currentRotation = Number(item.rotation || 0);
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

function randomTransform(rotation = 0) {
  const angle = (Math.random() * 8 - 4).toFixed(2);
  const x = (Math.random() * 1.4 - .7).toFixed(2);
  const y = (Math.random() * 1.4 - .7).toFixed(2);
  return `translate(${x}vw, ${y}vh) rotate(${Number(rotation) + Number(angle)}deg) scale(.96)`;
}

async function setCollageTile(tile, item, transitionSeconds) {
  tile.classList.remove('loaded');
  tile.style.transitionDuration = `${transitionSeconds}s`;
  tile.style.transform = randomTransform(item.rotation);
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

function resizeVisualizer() {
  const scale = Math.min(1, 1920 / window.innerWidth, 1080 / window.innerHeight);
  const width = Math.max(640, Math.round(window.innerWidth * scale));
  const height = Math.max(360, Math.round(window.innerHeight * scale));
  if (visualizer.width !== width || visualizer.height !== height) {
    visualizer.width = width;
    visualizer.height = height;
  }
}

async function pollVisualizer() {
  try { visualizerData = await json('/api/visualizer/spectrum'); } catch (_error) { visualizerData.available = false; }
}

function visualizerLevels(time) {
  const sensitivity = Number(visualizerData.sensitivity || 100) / 100;
  if (visualizerData.available) {
    return ['bass', 'mid', 'treble', 'volume'].map(name => Math.min(1.5, Number(visualizerData[name] || 0) * sensitivity));
  }
  return [
    .46 + Math.sin(time * 2.1) * .22,
    .42 + Math.sin(time * 1.37 + 2) * .2,
    .38 + Math.sin(time * 3.4 + 1) * .18,
    .52 + Math.sin(time * .83) * .12,
  ];
}

function drawKaleidoscope(ctx, width, height, time, bass, mid, treble) {
  const arms = 16;
  const radius = Math.min(width, height) * (.22 + bass * .18);
  ctx.save();
  ctx.translate(width / 2, height / 2);
  ctx.rotate(time * (.12 + treble * .24));
  ctx.globalCompositeOperation = 'lighter';
  for (let arm = 0; arm < arms; arm += 1) {
    ctx.save();
    ctx.rotate(arm * Math.PI * 2 / arms);
    for (let index = 0; index < 18; index += 1) {
      const phase = time * (1.5 + treble) + index * .5;
      const distance = radius * index / 17;
      const size = 3 + (bass * 18 + mid * 11) * (1 + Math.sin(phase)) / 2;
      ctx.fillStyle = `hsla(${(time * 55 + arm * 24 + index * 9) % 360},100%,${55 + treble * 25}%,.5)`;
      ctx.beginPath();
      ctx.arc(distance, Math.sin(phase) * radius * .12, size, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  }
  ctx.restore();
}

function drawPlasma(ctx, width, height, time, bass, mid, treble) {
  ctx.globalCompositeOperation = 'lighter';
  for (let index = 0; index < 18; index += 1) {
    const phase = time * (.35 + index * .013);
    const x = width * (.5 + Math.sin(phase * 1.7 + index) * .38);
    const y = height * (.5 + Math.cos(phase * 1.23 + index * .7) * .38);
    const radius = Math.min(width, height) * (.05 + bass * .13 + (index % 4) * .012);
    const gradient = ctx.createRadialGradient(x, y, 0, x, y, radius);
    gradient.addColorStop(0, `hsla(${(time * 45 + index * 31) % 360},100%,65%,${.2 + mid * .28})`);
    gradient.addColorStop(1, 'transparent');
    ctx.fillStyle = gradient;
    ctx.fillRect(x - radius, y - radius, radius * 2, radius * 2);
  }
}

function drawTunnel(ctx, width, height, time, bass, mid, treble) {
  ctx.save();
  ctx.translate(width / 2 + Math.sin(time) * width * .06, height / 2 + Math.cos(time * .7) * height * .06);
  ctx.rotate(time * .08);
  for (let ring = 0; ring < 36; ring += 1) {
    const progress = (ring / 36 + time * (.08 + bass * .04)) % 1;
    const radius = progress * Math.max(width, height) * .72;
    ctx.strokeStyle = `hsla(${(ring * 17 + time * 70) % 360},100%,${48 + treble * 30}%,${1 - progress})`;
    ctx.lineWidth = 1 + (1 - progress) * (4 + mid * 10);
    ctx.beginPath();
    const sides = 6 + (ring % 5);
    for (let side = 0; side <= sides; side += 1) {
      const angle = side * Math.PI * 2 / sides;
      const x = Math.cos(angle) * radius;
      const y = Math.sin(angle) * radius;
      if (!side) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.stroke();
  }
  ctx.restore();
}

function drawStarfield(ctx, width, height, time, bass, mid, treble) {
  ctx.save();
  ctx.translate(width / 2, height / 2);
  ctx.globalCompositeOperation = 'lighter';
  const limit = Math.max(width, height) * .72;
  for (const star of stars) {
    star.radius += (.0015 + star.speed * .0015) * (1 + bass * 5);
    if (star.radius > 1) star.radius = .008;
    const distance = star.radius * star.radius * limit;
    const x = Math.cos(star.angle + time * .03) * distance;
    const y = Math.sin(star.angle + time * .03) * distance;
    const size = star.size * (.5 + star.radius * 4) * (1 + treble);
    ctx.fillStyle = `hsla(${(time * 38 + star.angle * 57 + mid * 90) % 360},100%,75%,${.25 + star.radius * .7})`;
    ctx.fillRect(x, y, size, size);
  }
  ctx.restore();
}

function drawVisualizer(milliseconds) {
  if (displayMode !== 'visualizer') return;
  resizeVisualizer();
  const time = milliseconds / 1000;
  const {width, height} = visualizer;
  const [bass, mid, treble, volume] = visualizerLevels(time);
  visualizerContext.globalCompositeOperation = 'source-over';
  visualizerContext.fillStyle = `rgba(2,0,9,${.11 + (1 - volume) * .08})`;
  visualizerContext.fillRect(0, 0, width, height);
  const style = visualizerData.style || 'kaleidoscope';
  if (style === 'plasma') drawPlasma(visualizerContext, width, height, time, bass, mid, treble);
  else if (style === 'tunnel') drawTunnel(visualizerContext, width, height, time, bass, mid, treble);
  else if (style === 'starfield') drawStarfield(visualizerContext, width, height, time, bass, mid, treble);
  else drawKaleidoscope(visualizerContext, width, height, time, bass, mid, treble);
  if (!visualizerData.available) {
    visualizerContext.globalCompositeOperation = 'source-over';
    visualizerContext.fillStyle = 'rgba(255,255,255,.42)';
    visualizerContext.font = `${Math.max(11, width / 120)}px system-ui`;
    visualizerContext.fillText('Demo mode · connect a USB microphone for live response', 22, height - 22);
  }
  visualizerFrame = requestAnimationFrame(drawVisualizer);
}

function stopVisualizer() {
  cancelAnimationFrame(visualizerFrame);
  clearInterval(visualizerPoll);
  visualizer.classList.remove('active');
}

async function startVisualizer() {
  hideSlideshow();
  hideCollage();
  caption.classList.remove('visible');
  message.classList.add('hidden');
  visualizer.classList.add('active');
  resizeVisualizer();
  visualizerContext.fillStyle = '#020006';
  visualizerContext.fillRect(0, 0, visualizer.width, visualizer.height);
  await pollVisualizer();
  visualizerPoll = setInterval(pollVisualizer, 90);
  visualizerFrame = requestAnimationFrame(drawVisualizer);
}

async function activateMode(mode, force = false) {
  if (!force && displayMode === mode) return;
  clearTimeout(timer);
  stopVisualizer();
  displayMode = mode;
  document.querySelector('#viewer').classList.toggle('sleeping', mode === 'sleep');
  if (mode === 'sleep') {
    hideSlideshow();
    hideCollage();
    message.classList.add('hidden');
    return;
  }
  if (mode === 'collage') {
    hideCollage();
    await showCollage();
  } else if (mode === 'photos') {
    hideCollage();
    currentPath = '';
    await showNext();
  } else if (mode === 'visualizer') {
    await startVisualizer();
  }
}

async function syncRuntime() {
  try {
    const state = await json('/api/runtime');
    if (state.display_mode !== displayMode) await activateMode(state.display_mode);
    if (commandSequence !== null && state.command_sequence !== commandSequence) {
      if (displayMode === 'collage') await rotateCollageItem();
      else await showNext(state.navigation || 'next');
    }
    commandSequence = state.command_sequence;
    if (state.current_path === currentPath && Number(state.rotation) !== currentRotation) {
      currentRotation = Number(state.rotation || 0);
      const activePhoto = document.querySelector('.photo.active');
      if (activePhoto) applyRotation(activePhoto, currentRotation);
    }
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
