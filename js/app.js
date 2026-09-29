import { TourEngine } from './engine.js';
import { Narrator } from './narrator.js';
import { GpsSource, SimulatedSource } from './location.js';
import { fetchIndex, loadPack, downloadPack, removePack, isDownloaded } from './packs.js';
import { drawRadar } from './radar.js';
import { distance, formatDistance } from './geo.js';

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
  units: 'imperial',
  lead: 20,
  ...store.get('settings', {}),
};

const state = {
  index: [],
  selectedId: store.get('selectedPack', null),
  pack: null,
  engine: null,
  source: null,
  running: false,
  lastFix: null,
  lastResult: null,
  wakeLock: null,
};

const narrator = new Narrator({ onChange: renderNow });
narrator.rate = settings.rate;

// ---------- Tour lifecycle ----------

async function startTour() {
  const entry = state.index.find((p) => p.id === state.selectedId);
  if (!entry) return setStatus('Pick a tour first', 'error');
  narrator.unlock(); // must happen inside the tap handler

  try {
    state.pack = await loadPack(entry);
  } catch (err) {
    return setStatus(err.message, 'error');
  }
  // Simulated drives start fresh and never touch the real trip's history.
  const played = settings.simulate ? [] : store.get(playedKey(), []);
  state.engine = new TourEngine(state.pack, { leadSeconds: settings.lead }, played);
  state.running = true;

  const onFix = (fix) => handleFix(fix);
  if (settings.simulate) {
    if (!state.pack.demoRoute?.length) return setStatus('This tour has no demo route', 'error');
    state.source = new SimulatedSource(state.pack.demoRoute, onFix, {
      timeScale: Number(settings.simSpeed),
      onEnd: () => setStatus('Simulated drive finished', 'idle'),
    });
    setStatus('Simulating drive…', 'live');
  } else {
    state.source = new GpsSource(onFix, (err) => setStatus(gpsErrorMessage(err), 'error'));
    setStatus('Waiting for GPS…', 'live');
  }
  state.source.start();
  requestWakeLock();
  renderNow();
}

function stopTour() {
  state.source?.stop();
  state.source = null;
  state.running = false;
  narrator.stop();
  state.wakeLock?.release().catch(() => {});
  state.wakeLock = null;
  setStatus('Idle', 'idle');
  renderControls();
}

function handleFix(fix) {
  state.lastFix = fix;
  const result = state.engine.update(fix);
  state.lastResult = result;

  for (const item of result.triggered) {
    narrator.enqueue({
      id: item.id,
      title: item.name,
      text: item.story,
      category: item.ambient ? 'Did you know?' : item.category,
      // If the queue is long and you've already left the area, skip the story.
      stillRelevant: () =>
        item.ambient || item.lat == null || !state.lastFix ||
        distance(state.lastFix, item) <= item.radius + 2000,
    });
  }
  if (result.triggered.length && state.source instanceof GpsSource) store.set(playedKey(), [...state.engine.played]);

  if (state.source instanceof GpsSource) {
    const speed = fix.speed != null ? fix.speed : 0;
    const mph = settings.units === 'metric' ? `${Math.round(speed * 3.6)} km/h` : `${Math.round(speed * 2.237)} mph`;
    setStatus(result.insidePark ? `GPS live · ${mph}` : `Outside tour area · ${mph}`, 'live');
  } else if (state.source) {
    setStatus(`Simulating · ${Math.round(state.source.progress * 100)}%`, 'live');
  }
  renderNearby();
}

const playedKey = () => `played:${state.pack?.id ?? state.selectedId}`;

function gpsErrorMessage(err) {
  if (err?.code === 1) return 'Location permission denied';
  if (err?.code === 2) return 'No GPS signal yet';
  if (err?.code === 3) return 'GPS timed out, still trying';
  return err?.message ?? 'GPS error';
}

async function requestWakeLock() {
  // Browsers pause GPS when the screen turns off, so keep it on during a tour.
  try {
    state.wakeLock = await navigator.wakeLock?.request('screen');
  } catch { /* not supported or denied */ }
}

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible' && state.running && !state.wakeLock) requestWakeLock();
  if (document.visibilityState === 'visible' && state.wakeLock?.released) requestWakeLock();
});

// ---------- Rendering ----------

function setStatus(text, kind) {
  $('status-text').textContent = text;
  $('status-dot').className = `dot ${kind === 'live' ? 'live' : kind === 'error' ? 'error' : ''}`;
}

function renderControls() {
  const btn = $('btn-start');
  btn.textContent = state.running ? 'Stop tour' : 'Start tour';
  btn.classList.toggle('stop', state.running);
  $('btn-skip').disabled = !narrator.current;
}

function renderNow() {
  const item = narrator.current;
  if (item) {
    $('now-eyebrow').textContent = narrator.muted ? 'Now showing (muted)' : `Now playing${item.category ? ` · ${item.category}` : ''}`;
    $('now-title').textContent = item.title;
    $('now-text').textContent = item.text;
  } else if (state.running) {
    $('now-eyebrow').textContent = 'Listening for places';
    $('now-title').textContent = state.pack?.name ?? 'On tour';
    $('now-text').textContent = 'Enjoy the drive. The next story will start automatically as you approach.';
  }
  const q = narrator.queue.length;
  $('queue-info').hidden = q === 0;
  $('queue-info').textContent = q === 1 ? '1 more story queued' : `${q} more stories queued`;
  $('btn-mute').textContent = narrator.muted ? '🔇' : '🔊';
  $('btn-mute').setAttribute('aria-pressed', String(narrator.muted));
  renderControls();
}

function renderNearby() {
  const result = state.lastResult;
  // Zoom the radar out just far enough to show the next place you haven't heard yet.
  const nearest = result?.nearby[0]?.distance ?? 0;
  const range = [1000, 3000, 5000, 10000, 20000, 50000].find((r) => r >= nearest * 1.15) ?? 50000;
  drawRadar($('radar'), { fix: state.lastFix, places: result?.all ?? [], rangeMeters: range });
  $('radar-hint').textContent = `${formatDistance(range, settings.units)} radius · heading up`;
  const list = $('upcoming');
  if (!result) return;
  list.replaceChildren(
    ...(result.nearby.length
      ? result.nearby.map(({ poi, distance: d }) => {
          const li = document.createElement('li');
          const name = document.createElement('span');
          name.textContent = poi.name;
          const cat = document.createElement('span');
          cat.className = 'cat';
          cat.textContent = poi.category ?? '';
          name.append(cat);
          const dist = document.createElement('span');
          dist.className = 'dist';
          dist.textContent = formatDistance(d, settings.units);
          li.append(name, dist);
          return li;
        })
      : [Object.assign(document.createElement('li'), { className: 'empty', textContent: "You've heard every story on this tour 🎉" })]),
  );
}

async function renderPacks() {
  const list = $('packs');
  if (!state.index.length) {
    list.innerHTML = '<li class="empty">No tours available offline yet. Connect once to download one.</li>';
    return;
  }
  const items = await Promise.all(
    state.index.map(async (entry) => {
      const downloaded = await isDownloaded(entry);
      const li = document.createElement('li');
      li.classList.toggle('selected', entry.id === state.selectedId);

      const radio = document.createElement('input');
      radio.type = 'radio';
      radio.name = 'pack';
      radio.checked = entry.id === state.selectedId;
      radio.setAttribute('aria-label', `Select ${entry.name}`);
      radio.addEventListener('change', () => {
        state.selectedId = entry.id;
        store.set('selectedPack', entry.id);
        renderPacks();
      });

      const meta = document.createElement('label');
      meta.className = 'meta';
      meta.innerHTML = `<strong></strong><span></span>`;
      meta.querySelector('strong').textContent = entry.name;
      meta.querySelector('span').textContent = entry.region + (downloaded ? ' · ✓ Available offline' : ' · Needs signal until downloaded');
      meta.addEventListener('click', () => radio.click());

      const action = document.createElement('button');
      action.className = 'secondary';
      if (downloaded) {
        action.textContent = 'Remove';
        action.setAttribute('aria-label', `Remove offline copy of ${entry.name}`);
        action.onclick = async () => { await removePack(entry); renderPacks(); };
        li.append(radio, meta, action);
      } else {
        action.textContent = 'Download';
        action.onclick = async () => {
          action.disabled = true;
          action.textContent = 'Downloading…';
          try {
            await downloadPack(entry);
          } catch (err) {
            setStatus(err.message, 'error');
          }
          renderPacks();
        };
        li.append(radio, meta, action);
      }
      return li;
    }),
  );
  list.replaceChildren(...items);
}

// ---------- Wiring ----------

function bindSettings() {
  $('opt-sim').checked = settings.simulate;
  $('opt-sim-speed').value = String(settings.simSpeed);
  $('opt-rate').value = String(settings.rate);
  $('opt-units').value = settings.units;
  $('opt-lead').value = String(settings.lead);
  const save = () => store.set('settings', settings);

  $('opt-sim').addEventListener('change', (e) => { settings.simulate = e.target.checked; save(); });
  $('opt-sim-speed').addEventListener('change', (e) => { settings.simSpeed = Number(e.target.value); save(); });
  $('opt-rate').addEventListener('change', (e) => { settings.rate = narrator.rate = Number(e.target.value); save(); });
  $('opt-units').addEventListener('change', (e) => { settings.units = e.target.value; save(); renderNearby(); });
  $('opt-lead').addEventListener('change', (e) => {
    settings.lead = Number(e.target.value);
    if (state.engine) state.engine.opts.leadSeconds = settings.lead;
    save();
  });
  $('btn-reset').addEventListener('click', () => {
    store.set(playedKey(), []);
    state.engine?.reset();
    $('btn-reset').textContent = 'Cleared ✓';
    setTimeout(() => ($('btn-reset').textContent = 'Reset trip'), 1500);
  });
}

$('btn-start').addEventListener('click', () => (state.running ? stopTour() : startTour()));
$('btn-skip').addEventListener('click', () => narrator.skip());
$('btn-mute').addEventListener('click', () => narrator.setMuted(!narrator.muted));
window.addEventListener('resize', renderNearby);

function renderOnlineHint() {
  $('offline-hint').textContent = navigator.onLine
    ? 'Download a tour before you lose signal. Stories use your phone’s built-in voice, so no data is needed on the road.'
    : 'You’re offline. Downloaded tours still work.';
}
window.addEventListener('online', renderOnlineHint);
window.addEventListener('offline', renderOnlineHint);

async function init() {
  bindSettings();
  renderOnlineHint();
  drawRadar($('radar'), { fix: null, places: [] });
  try {
    state.index = (await fetchIndex()).packs;
  } catch {
    state.index = [];
  }
  if (!state.index.some((p) => p.id === state.selectedId)) state.selectedId = state.index[0]?.id ?? null;
  renderPacks();
  if (!narrator.supported) $('offline-hint').textContent += ' This browser has no text-to-speech; stories will be shown on screen only.';

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('sw.js').catch((err) => console.warn('Service worker failed', err));
  }
}

init();

// Handy for debugging from the console.
window.__tour = { state, narrator, settings };
