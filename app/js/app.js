import { TourEngine } from './engine.js';
import { EpisodePlayer } from './player.js';
import { GpsSource, SimulatedSource } from './location.js';
import { EngineClient } from './api.js';
import { loadCatalog, loadManifest, downloadPack, removePack, assetUrl } from './packs.js';
import { toEnginePack, episodeFor, nextStop, navigationUrl, CATEGORY_LABEL, formatBytes } from './tour.js';
import { distance, formatDistance } from './geo.js';
import { DriveMode } from './drive.js';

const $ = (id) => document.getElementById(id);

// localStorage can throw (private mode, blocked storage), so treat it as best-effort.
const store = {
  get(key, fallback) {
    try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* ignore */ }
  },
};

const settings = {
  simulate: false,
  simSpeed: 3,
  rate: 1,
  beds: 1,
  units: 'imperial',
  lead: 20,
  nav: 'auto',
  server: '',
  demo: false,
  ...store.get('settings', {}),
};
const saveSettings = () => store.set('settings', settings);

const state = {
  catalog: [],
  selectedId: store.get('selectedTour', null),
  manifest: null,
  href: null,
  engine: null,
  source: null,
  running: false,
  lastFix: null,
  queue: [],
  current: null,
  outroPlayed: false,
  wakeLock: null,
  engineClient: null,
  health: null,
  map: null,
  mode: 'tour', // 'tour' (a chosen tour) or 'drive' ("just drive": stories wherever you go)
  drive: null,
  stopIndex: new Map(), // drive mode: stop id -> { stop, href }
};

const player = new EpisodePlayer({
  resolve: (path) => assetUrl(state.href, path),
  onLine: (line) => renderLine(line),
});
player.speech.rate = settings.rate;
player.volume.beds = settings.beds;

// ---------- Tour lifecycle ----------

async function selectTour(id) {
  if (state.running) stopTour();
  state.selectedId = id;
  store.set('selectedTour', id);
  const entry = state.catalog.find((t) => t.id === id);
  renderTours();
  if (!entry) return;
  try {
    const { manifest, href } = await loadManifest(entry);
    state.manifest = manifest;
    state.href = href;
    player.speech.setStyle(manifest.voices);
    state.engine = null;
    await showMap();
    renderIdle();
    renderUpcoming();
  } catch (err) {
    setStatus(err.message, 'error');
  }
}

async function showMap(createOnly = false) {
  if (!state.map) {
    try {
      const { TourMap } = await import('./map.js');
      state.map = new TourMap($('map'), { onStopClick: (s) => previewStop(s) });
    } catch (err) {
      console.warn('Map unavailable', err);
      $('map').innerHTML = '<p class="map-fallback">Map unavailable on this device. Stories still play.</p>';
      return;
    }
  }
  if (createOnly || !state.manifest) return;
  state.mapFree = false;
  await state.map.show(state.manifest, (p) => assetUrl(state.href, p)).catch((err) => console.warn(err));
}

function startTour() {
  if (!state.manifest) return setStatus('Pick a tour first', 'error');
  player.unlock(); // must happen inside the tap handler
  state.mode = 'tour';
  if (state.mapFree) showMap(); // coming back from "just drive": show this tour's map again

  // Simulated drives start fresh and never touch the real trip's history.
  const played = settings.simulate ? [] : store.get(playedKey(), []);
  state.engine = new TourEngine(toEnginePack(state.manifest), { leadSeconds: settings.lead }, played);
  state.queue = [];
  state.outroPlayed = false;
  state.running = true;

  if (settings.simulate) {
    const route = state.manifest.route?.length
      ? state.manifest.route.map(([lat, lon]) => ({ lat, lon }))
      : state.manifest.stops.map((s) => ({ lat: s.trigger.lat, lon: s.trigger.lon }));
    state.source = new SimulatedSource(route, handleFix, {
      timeScale: Number(settings.simSpeed),
      onEnd: () => setStatus('Simulated drive finished', 'idle'),
    });
    setStatus('Simulating drive…', 'live');
  } else {
    state.source = new GpsSource(handleFix, (err) => setStatus(gpsErrorMessage(err), 'error'));
    setStatus('Waiting for GPS…', 'live');
  }
  state.source.start();
  requestWakeLock();
  $('btn-recenter').hidden = false;
  renderIdle();
}

/** "Just drive": no tour needed. Stories for the road ahead are fetched as you go. */
async function startDrive() {
  player.unlock();
  if (state.running) stopTour();
  state.mode = 'drive';
  state.mapFree = false; // the free map is shown on the first GPS fix
  state.stopIndex = new Map();
  // Remembered across drives, so your daily commute doesn't repeat itself.
  const played = settings.simulate ? [] : store.get('played:drive', []);
  state.engine = new TourEngine({ id: 'drive', name: 'Just drive', bounds: null, intro: null, pois: [], ambient: [] }, { leadSeconds: settings.lead }, played);
  state.queue = [];
  state.running = true;
  state.drive = new DriveMode({
    engineBase: state.engineClient?.base ?? null,
    source: settings.demo ? 'fixtures' : undefined,
    onCell: (manifest, href) => addCell(manifest, href),
    onChange: () => renderDriveStatus(),
  });

  // Simulation drives the selected tour's road, which is handy for trying it at home.
  const route = settings.simulate ? state.manifest?.route?.map(([lat, lon]) => ({ lat, lon })) : null;
  if (route?.length) {
    state.source = new SimulatedSource(route, handleFix, { timeScale: Number(settings.simSpeed), onEnd: () => setStatus('Simulated drive finished', 'idle') });
  } else {
    state.source = new GpsSource(handleFix, (err) => setStatus(gpsErrorMessage(err), 'error'));
  }
  setStatus(route?.length ? 'Simulating a free drive…' : 'Waiting for GPS…', 'live');
  state.source.start();
  requestWakeLock();
  $('btn-recenter').hidden = false;
  renderIdle();
  renderUpcoming();
}

function addCell(manifest, href) {
  const pack = toEnginePack(manifest);
  for (const s of manifest.stops) state.stopIndex.set(s.id, { stop: s, href });
  state.engine?.addPois(pack.pois);
  state.map?.addStops(manifest.stops);
  renderUpcoming();
}

function renderDriveStatus() {
  if (state.mode !== 'drive' || !state.drive || !(state.source instanceof GpsSource) || !state.lastFix) return;
  const { ready, loading } = state.drive.stats;
  setStatus(`Just driving · ${state.stopIndex.size} places nearby${loading ? ' · fetching…' : ''}${!ready && !navigator.onLine ? ' · offline' : ''}`, 'live');
}

function stopTour() {
  state.drive?.stop();
  state.drive = null;
  state.source?.stop();
  state.source = null;
  state.running = false;
  state.queue = [];
  player.stop();
  state.wakeLock?.release().catch(() => {});
  state.wakeLock = null;
  setStatus('Idle', 'idle');
  renderIdle();
}

function handleFix(fix) {
  state.lastFix = fix;
  if (state.mode === 'drive') {
    if (!state.mapFree) {
      state.mapFree = true;
      showFreeMap(fix);
    }
    state.drive?.update(fix);
  }
  const result = state.engine.update(fix);
  for (const t of result.triggered) enqueue(t.id);
  if (result.triggered.length && state.source instanceof GpsSource) store.set(playedKey(), [...state.engine.played]);

  if (state.mode === 'drive') {
    if (state.source instanceof GpsSource) renderDriveStatus();
    else setStatus(`Free drive · ${state.stopIndex.size} places · ${Math.round(state.source.progress * 100)}%`, 'live');
  } else if (state.source instanceof GpsSource) {
    const speed = fix.speed ?? 0;
    const v = settings.units === 'metric' ? `${Math.round(speed * 3.6)} km/h` : `${Math.round(speed * 2.237)} mph`;
    setStatus(result.insidePark ? `GPS live · ${v}` : `Outside tour area · ${v}`, 'live');
  } else if (state.source) {
    setStatus(`Simulating · ${Math.round(state.source.progress * 100)}%`, 'live');
  }
  state.map?.setPosition(fix);
  renderUpcoming();
  maybePlayOutro();
}

const playedKey = () => (state.mode === 'drive' ? 'played:drive' : `played:${state.manifest?.id ?? state.selectedId}`);

async function showFreeMap(fix) {
  if (!state.map) await showMap(true);
  await state.map?.showFree(fix).catch((err) => console.warn(err));
  for (const { stop } of state.stopIndex.values()) state.map?.addStops([stop]);
}

// ---------- Story queue ----------

function lookup(id) {
  if (state.mode === 'drive') {
    const hit = state.stopIndex.get(id);
    return hit ? { id, title: hit.stop.name, kind: 'stop', stop: hit.stop, episode: hit.stop.episode, href: hit.href } : null;
  }
  const item = episodeFor(state.manifest, id);
  return item ? { ...item, href: state.href } : null;
}

function enqueue(id) {
  const item = lookup(id);
  if (!item) return;
  if (state.current?.id === id || state.queue.some((q) => q.id === id)) return;
  // If you've already driven well past a place by the time its turn comes, skip it.
  item.stillRelevant = () =>
    item.kind !== 'stop' || !state.lastFix ||
    distance(state.lastFix, item.stop.trigger) <= item.stop.trigger.radius + 2000;
  state.queue.push(item);
  player.preload(item.episode, item.href);
  if (!state.current) playNext();
  else renderQueue();
}

async function playNext() {
  let item = state.queue.shift();
  while (item && !item.stillRelevant()) item = state.queue.shift();
  state.current = item ?? null;
  renderNow();
  if (!item) return;
  setMediaSession(item);
  await player.play(item.episode, { base: item.href ?? state.href });
  if (state.current === item) state.current = null;
  if (state.running) setTimeout(playNext, 900);
  else renderIdle();
}

function skip() {
  player.stop();
}

function maybePlayOutro() {
  const m = state.manifest;
  if (state.mode === 'drive' || state.outroPlayed || !m?.outro?.audio || !state.engine) return;
  if (!m.stops.every((s) => state.engine.played.has(s.id)) || state.current || state.queue.length) return;
  state.outroPlayed = true;
  state.queue.push({
    id: '__outro',
    kind: 'outro',
    title: 'That’s the tour',
    stillRelevant: () => true,
    episode: { segments: [{ type: 'pause', seconds: 1.5 }, { type: 'music', audio: m.outro.audio, gain: 0.7, wait: true }] },
  });
  playNext();
}

// ---------- Rendering ----------

function setStatus(text, kind) {
  $('status-text').textContent = text;
  $('status-dot').className = `dot ${kind === 'live' ? 'live' : kind === 'error' ? 'error' : ''}`;
}

function renderIdle() {
  if (state.current) renderStartButton();
  else renderNow();
}

function renderNow() {
  const item = state.current;
  const m = state.manifest;
  $('btn-skip').disabled = !item;
  if (item) {
    const cat = item.stop ? CATEGORY_LABEL[item.stop.category] ?? '' : item.kind === 'ambient' ? 'Did you know?' : '';
    const side = item.stop?.side ? ` · on your ${item.stop.side}` : '';
    $('now-eyebrow').textContent = `${player.muted ? 'Muted' : 'Now playing'}${cat ? ` · ${cat}` : ''}${side}`;
    $('now-title').textContent = item.title;
  } else if (state.mode === 'drive' && state.running) {
    $('now-eyebrow').textContent = 'Just driving';
    $('now-title').textContent = 'Stories wherever you go';
    renderLine(null);
    $('now-text').textContent = 'No plan needed. Cairn fetches stories for the road ahead as you drive and plays them as you pass.';
  } else if (m) {
    $('now-eyebrow').textContent = state.running ? 'Listening for places' : `${m.stops.length} stories · ${m.region || 'Tour'}`;
    $('now-title').textContent = m.name;
    renderLine(null);
    $('now-text').textContent = state.running
      ? 'Enjoy the drive. The next story starts on its own as you approach.'
      : m.description || 'Press Start when you set off. Stories play as you reach each place.';
  }
  state.map?.setPlayed(state.engine?.played ?? new Set(), item?.stop?.id);
  renderQueue();
  renderStartButton();
}

function renderStartButton() {
  const btn = $('btn-start');
  const driving = state.running && state.mode === 'drive';
  btn.textContent = driving ? 'Stop driving' : state.running ? 'Stop tour' : 'Start tour';
  btn.classList.toggle('stop', state.running);
  btn.disabled = !state.manifest && !state.running;
  $('btn-drive').hidden = state.running;
}

function renderLine(line) {
  const el = $('now-line');
  el.classList.toggle('storyteller', line?.voice === 'storyteller');
  $('now-who').textContent = line ? (line.voice === 'storyteller' ? 'Storyteller' : 'Narrator') : '';
  if (line) $('now-text').textContent = line.text;
}

function renderQueue() {
  const q = state.queue.length;
  $('queue-info').hidden = q === 0;
  $('queue-info').textContent = q === 1 ? `Up next: ${state.queue[0].title}` : `${q} stories queued`;
}

function renderUpcoming() {
  if (state.mode === 'drive' && state.running) return renderUpcomingDrive();
  const m = state.manifest;
  const list = $('upcoming');
  if (!m) return;
  const played = state.engine?.played ?? new Set();
  const fix = state.lastFix;
  const items = m.stops
    .slice()
    .sort((a, b) => a.order - b.order)
    .map((s, i) => {
      const li = document.createElement('li');
      li.classList.toggle('played', played.has(s.id));
      const num = document.createElement('span');
      num.className = 'num';
      num.textContent = String(i + 1);
      const body = document.createElement('span');
      const title = document.createElement('span');
      title.className = 'title';
      title.textContent = s.name;
      const meta = document.createElement('span');
      meta.className = 'meta';
      meta.textContent = [CATEGORY_LABEL[s.category], s.summary].filter(Boolean).join(' · ');
      body.append(title, meta);
      const dist = document.createElement('span');
      dist.className = 'dist';
      dist.textContent = played.has(s.id) ? 'Heard' : fix ? formatDistance(distance(fix, s.place), settings.units) : '';
      li.append(num, body, dist);
      return li;
    });
  list.replaceChildren(...items);
  const next = nextStop(m, played, fix);
  $('nav-next').hidden = !next;
  if (next) {
    $('nav-next').href = navigationUrl(next.stop.place, settings.nav);
    $('nav-next').textContent = `Navigate to ${next.stop.name}`;
  }
}

/** Drive mode: the nearest places we know about that you haven't heard yet. */
function renderUpcomingDrive() {
  const list = $('upcoming');
  const fix = state.lastFix;
  const played = state.engine?.played ?? new Set();
  $('nav-next').hidden = true;
  const near = [...state.stopIndex.values()]
    .map(({ stop }) => ({ stop, d: fix ? distance(fix, stop.place) : Infinity }))
    .filter(({ stop }) => !played.has(stop.id))
    .sort((a, b) => a.d - b.d)
    .slice(0, 6);
  if (!near.length) {
    list.innerHTML = `<li class="empty">${fix ? 'Looking for stories along the road ahead…' : 'Waiting for your location…'}</li>`;
    return;
  }
  list.replaceChildren(
    ...near.map(({ stop, d }) => {
      const li = document.createElement('li');
      const num = document.createElement('span');
      num.className = 'num dot';
      const body = document.createElement('span');
      const title = document.createElement('span');
      title.className = 'title';
      title.textContent = stop.name;
      const meta = document.createElement('span');
      meta.className = 'meta';
      meta.textContent = [CATEGORY_LABEL[stop.category], stop.summary].filter(Boolean).join(' · ');
      body.append(title, meta);
      const dist = document.createElement('span');
      dist.className = 'dist';
      dist.textContent = Number.isFinite(d) ? formatDistance(d, settings.units) : '';
      li.append(num, body, dist);
      return li;
    }),
  );
}

function previewStop(stop) {
  if (state.current) return;
  $('now-eyebrow').textContent = `Stop ${stop.order + 1} · ${CATEGORY_LABEL[stop.category] ?? ''}`;
  $('now-title').textContent = stop.name;
  renderLine(null);
  $('now-text').textContent = stop.summary ? `${stop.summary}.` : 'A story plays here when you arrive.';
}

async function renderTours() {
  const list = $('tours');
  if (!state.catalog.length) {
    list.innerHTML = '<li class="empty">No tours yet. Search for a place above to create one.</li>';
    return;
  }
  list.replaceChildren(
    ...state.catalog.map((entry) => {
      const li = document.createElement('li');
      li.classList.toggle('selected', entry.id === state.selectedId);
      const radio = document.createElement('input');
      radio.type = 'radio';
      radio.name = 'tour';
      radio.checked = entry.id === state.selectedId;
      radio.setAttribute('aria-label', `Select ${entry.name}`);
      radio.addEventListener('change', () => selectTour(entry.id));

      const meta = document.createElement('div');
      meta.className = 'meta';
      const name = document.createElement('strong');
      name.textContent = entry.name;
      const info = document.createElement('span');
      const km = entry.routeLengthM ? formatDistance(entry.routeLengthM, settings.units) : null;
      info.textContent = [entry.region, `${entry.stops} stories`, km, formatBytes(entry.totalBytes)].filter(Boolean).join(' · ');
      meta.append(name, info);
      if (entry.downloaded) {
        const off = document.createElement('span');
        off.className = 'offline';
        off.textContent = ' · ✓ Offline';
        info.append(off);
      }
      meta.addEventListener('click', () => radio.click());

      const action = document.createElement('button');
      action.className = 'secondary';
      action.type = 'button';
      if (entry.downloaded) {
        action.textContent = 'Remove';
        action.setAttribute('aria-label', `Remove offline copy of ${entry.name}`);
        action.onclick = async () => { await removePack(entry); entry.downloaded = false; renderTours(); };
      } else {
        action.textContent = 'Download';
        action.setAttribute('aria-label', `Download ${entry.name} for offline use`);
        action.onclick = () => download(entry, li, action);
      }
      li.append(radio, meta, action);
      return li;
    }),
  );
}

async function download(entry, li, button) {
  button.disabled = true;
  button.textContent = 'Downloading…';
  const bar = document.createElement('progress');
  bar.max = 1;
  li.querySelector('.meta').append(bar);
  try {
    await downloadPack(entry, (done, total) => { bar.value = total ? done / total : 0; });
    entry.downloaded = true;
  } catch (err) {
    setStatus(err.message, 'error');
  }
  renderTours();
}

// ---------- Creating tours on the engine ----------

async function createTour(body) {
  if (!state.engineClient) return setStatus('No tour engine configured (Settings)', 'error');
  if (settings.demo) body.source = 'fixtures';
  $('build').hidden = false;
  $('build-progress').value = 0;
  $('build-text').textContent = 'Asking the engine…';
  try {
    const job = await state.engineClient.buildTour(body, (j) => {
      $('build-progress').value = j.progress ?? 0;
      $('build-text').textContent = j.message ?? '';
    });
    await refreshCatalog();
    await selectTour(job.tour_id);
    const entry = state.catalog.find((t) => t.id === job.tour_id);
    $('build-text').textContent = 'Ready. Downloading for offline use…';
    if (entry && !entry.downloaded) {
      await downloadPack(entry, (d, t) => { $('build-progress').value = t ? d / t : 1; });
      entry.downloaded = true;
      renderTours();
    }
    $('build-text').textContent = `“${entry?.name ?? 'Tour'}” is ready and saved offline.`;
  } catch (err) {
    $('build-text').textContent = err.message;
  }
}

async function refreshCatalog() {
  state.catalog = await loadCatalog(state.engineClient ? state.engineClient.base : null);
  if (!state.catalog.some((t) => t.id === state.selectedId)) state.selectedId = state.catalog[0]?.id ?? null;
  renderTours();
}

async function connectEngine() {
  const candidates = [settings.server, location.origin].filter(Boolean);
  state.engineClient = null;
  state.health = null;
  for (const url of candidates) {
    try {
      const client = new EngineClient(url);
      state.health = await client.health();
      state.engineClient = client;
      break;
    } catch { /* try next */ }
  }
  const ok = !!state.engineClient;
  $('search').querySelector('button').disabled = !ok;
  $('plan').querySelector('button').disabled = !ok;
  $('btn-near').disabled = !ok;
  $('row-demo').hidden = !state.health?.fixtures?.length;
  $('engine-hint').textContent = ok
    ? `Engine connected · narration: ${state.health.tts === 'device' ? 'on-device voices' : 'studio voices'}`
    : navigator.onLine ? 'No tour engine reachable. Set one in Settings.' : 'Offline: your downloaded tours still work.';
}

// ---------- Platform integrations ----------

function gpsErrorMessage(err) {
  if (err?.code === 1) return 'Location permission denied';
  if (err?.code === 2) return 'No GPS signal yet';
  if (err?.code === 3) return 'GPS timed out, still trying';
  return err?.message ?? 'GPS error';
}

async function requestWakeLock() {
  // Browsers pause GPS when the screen turns off, so keep it on during a tour.
  try { state.wakeLock = await navigator.wakeLock?.request('screen'); } catch { /* unsupported */ }
}

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && state.running && (!state.wakeLock || state.wakeLock.released)) requestWakeLock();
});

function setMediaSession(item) {
  if (!('mediaSession' in navigator)) return;
  try {
    navigator.mediaSession.metadata = new MediaMetadata({
      title: item.title,
      artist: state.mode === 'drive' ? 'Just drive' : state.manifest?.name ?? 'Cairn',
      album: 'Cairn',
      artwork: [{ src: 'icons/icon-512.png', sizes: '512x512', type: 'image/png' }],
    });
    navigator.mediaSession.setActionHandler('nexttrack', skip);
  } catch { /* partial support */ }
}

// ---------- Wiring ----------

function bindSettings() {
  const bind = (id, key, parse = (v) => v, after = () => {}) => {
    const el = $(id);
    if (el.type === 'checkbox') el.checked = !!settings[key];
    else el.value = String(settings[key]);
    el.addEventListener('change', () => {
      settings[key] = el.type === 'checkbox' ? el.checked : parse(el.value);
      saveSettings();
      after();
    });
  };
  bind('opt-sim', 'simulate');
  bind('opt-sim-speed', 'simSpeed', Number);
  bind('opt-rate', 'rate', Number, () => { player.speech.rate = settings.rate; });
  bind('opt-beds', 'beds', Number, () => { player.volume.beds = settings.beds; });
  bind('opt-units', 'units', String, () => { renderUpcoming(); renderTours(); });
  bind('opt-lead', 'lead', Number, () => { if (state.engine) state.engine.opts.leadSeconds = settings.lead; });
  bind('opt-nav', 'nav', String, renderUpcoming);
  bind('opt-server', 'server', (v) => v.trim(), async () => { await connectEngine(); await refreshCatalog(); });
  bind('opt-demo', 'demo');

  $('btn-reset').addEventListener('click', () => {
    store.set(playedKey(), []);
    state.engine?.reset();
    renderUpcoming();
    $('btn-reset').textContent = 'Cleared ✓';
    setTimeout(() => ($('btn-reset').textContent = 'Reset trip'), 1500);
  });
  $('btn-voices').addEventListener('click', async () => {
    player.unlock();
    await player.speech.speak('I’m the narrator. I’ll tell you what’s coming up.', 'narrator');
    await player.speech.speak('And I’m the storyteller. I’ll tell you why it matters.', 'storyteller');
  });
  const v = player.speech.voices;
  const describe = () => {
    $('voice-info').textContent = player.speechSupported
      ? `On-device: ${v.narrator?.name ?? 'default'} and ${v.storyteller?.name ?? 'default'}`
      : 'This browser has no speech; tours with studio voices still play.';
  };
  describe();
  window.speechSynthesis?.addEventListener?.('voiceschanged', describe);
}

$('btn-start').addEventListener('click', () => (state.running ? stopTour() : startTour()));
$('btn-drive').addEventListener('click', startDrive);
$('plan').addEventListener('submit', (e) => {
  e.preventDefault();
  const to = $('plan-to').value.trim();
  const from = $('plan-from').value.trim();
  if (!to) return;
  if (from) return createTour({ origin: from, destination: to });
  // No start given: drive from where you are now.
  $('build').hidden = false;
  $('build-text').textContent = 'Finding your location…';
  navigator.geolocation.getCurrentPosition(
    (pos) => createTour({ origin: [pos.coords.latitude, pos.coords.longitude], destination: to }),
    (err) => { $('build-text').textContent = `${gpsErrorMessage(err)}. Type a starting point instead.`; },
    { enableHighAccuracy: false, timeout: 15000 },
  );
});
$('btn-skip').addEventListener('click', skip);
$('btn-mute').addEventListener('click', () => {
  player.setMuted(!player.muted);
  $('btn-mute').setAttribute('aria-pressed', String(player.muted));
  $('btn-mute').classList.toggle('muted', player.muted);
  renderNow();
});
$('btn-recenter').addEventListener('click', () => state.map?.recenter());
$('search').addEventListener('submit', (e) => {
  e.preventDefault();
  const q = $('search-q').value.trim();
  if (q) createTour({ query: q });
});
$('btn-near').addEventListener('click', () => {
  $('build').hidden = false;
  $('build-text').textContent = 'Finding your location…';
  navigator.geolocation.getCurrentPosition(
    (pos) => createTour({ center: [pos.coords.latitude, pos.coords.longitude], radius_m: 20000, name: 'Around you' }),
    (err) => { $('build-text').textContent = gpsErrorMessage(err); },
    { enableHighAccuracy: false, timeout: 15000 },
  );
});
window.addEventListener('resize', () => state.map?.resize());

function renderOnlineHint() {
  $('offline-hint').textContent = navigator.onLine
    ? 'Download a tour before you lose signal. Everything (stories, sound and map route) is saved on your phone.'
    : 'You’re offline. Downloaded tours still work.';
}
window.addEventListener('online', () => { renderOnlineHint(); connectEngine(); });
window.addEventListener('offline', renderOnlineHint);

async function init() {
  bindSettings();
  renderOnlineHint();
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch((err) => console.warn('Service worker failed', err));
  await connectEngine();
  await refreshCatalog();
  if (state.selectedId) await selectTour(state.selectedId);
}

init();

// Handy for debugging from the console.
window.__tour = { state, player, settings };
