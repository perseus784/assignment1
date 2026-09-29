// Tour packs (Cairn Tour Format v2) — listing, downloading for offline use, loading.
// A pack is a manifest.json plus audio files; "downloading" stores every file in the Cache API,
// where the service worker serves them from when there's no signal.

export const TOUR_CACHE = 'cairn-tours-v1';
export const FORMAT = 'cairn.tour/2';
const BUNDLED_INDEX = 'packs/index.json';

const hasCaches = () => typeof caches !== 'undefined';
const openCache = () => caches.open(TOUR_CACHE);

/** Absolute URL of the manifest for a catalog entry. */
export const manifestUrl = (entry) => new URL(entry.manifest, entry.base ?? location.href).href;

/** Resolve an asset path inside a pack to an absolute URL. */
export const assetUrl = (manifestHref, path) => new URL(path, manifestHref).href;

async function fetchJson(url) {
  const res = await fetch(url, { cache: 'no-cache' });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

/**
 * Catalog = tours on the engine server + packs bundled with the app + anything downloaded.
 * Works offline: falls back to what's in the cache.
 */
export async function loadCatalog(serverUrl) {
  const entries = new Map();
  const add = (list, base, origin) => {
    for (const t of list ?? []) if (!entries.has(t.id)) entries.set(t.id, { ...t, base, origin });
  };
  const tasks = [
    fetchJson(new URL(BUNDLED_INDEX, location.href).href)
      .then((d) => add(d.tours, new URL(BUNDLED_INDEX, location.href).href, 'bundled'))
      .catch(() => {}),
  ];
  if (serverUrl) {
    const base = serverUrl.replace(/\/?$/, '/');
    tasks.unshift(fetchJson(`${base}v1/tours`).then((d) => add(d.tours, base, 'server')).catch(() => {}));
  }
  await Promise.all(tasks);
  for (const saved of loadSavedEntries()) if (!entries.has(saved.id)) entries.set(saved.id, saved);
  const list = [...entries.values()];
  await Promise.all(list.map(async (e) => { e.downloaded = await isDownloaded(e); }));
  return list;
}

// Remember catalog entries we've downloaded, so they're listed even when fully offline.
const SAVED_KEY = 'cairn:downloaded';
function loadSavedEntries() {
  try { return JSON.parse(localStorage.getItem(SAVED_KEY)) ?? []; } catch { return []; }
}
function saveEntry(entry, keep) {
  try {
    const rest = loadSavedEntries().filter((e) => e.id !== entry.id);
    localStorage.setItem(SAVED_KEY, JSON.stringify(keep ? [...rest, { ...entry, downloaded: undefined }] : rest));
  } catch { /* storage unavailable */ }
}

export async function isDownloaded(entry) {
  if (!hasCaches()) return false;
  const cache = await openCache();
  const res = await cache.match(manifestUrl(entry));
  if (!res) return false;
  const m = await res.json();
  const keys = new Set((await cache.keys()).map((r) => r.url));
  return m.assets.every((a) => keys.has(assetUrl(manifestUrl(entry), a.path)));
}

/** Download a pack and all of its audio. Reports progress as bytes. */
export async function downloadPack(entry, onProgress = () => {}, { remember = true } = {}) {
  const href = manifestUrl(entry);
  const manifestRes = await fetch(href, { cache: 'reload' });
  if (!manifestRes.ok) throw new Error(`Couldn't download the tour (${manifestRes.status})`);
  const manifest = await manifestRes.clone().json();
  validateManifest(manifest);
  const cache = hasCaches() ? await openCache() : null;
  const total = manifest.totalBytes || manifest.assets.reduce((s, a) => s + a.bytes, 0);
  let done = 0;
  const queue = [...manifest.assets];
  const worker = async () => {
    while (queue.length) {
      const asset = queue.shift();
      const url = assetUrl(href, asset.path);
      if (cache && (await cache.match(url))) {
        done += asset.bytes;
      } else {
        const res = await fetch(url, { cache: 'reload' });
        if (!res.ok) throw new Error(`Download failed: ${asset.path} (${res.status})`);
        if (cache) await cache.put(url, res.clone());
        done += asset.bytes;
      }
      onProgress(done, total);
    }
  };
  await Promise.all([worker(), worker(), worker(), worker()]);
  if (cache) await cache.put(href, manifestRes);
  if (remember) saveEntry(entry, true);
  return manifest;
}

export async function removePack(entry) {
  if (!hasCaches()) return;
  const cache = await openCache();
  const prefix = new URL('.', manifestUrl(entry)).href;
  await Promise.all((await cache.keys()).filter((r) => r.url.startsWith(prefix)).map((r) => cache.delete(r)));
  saveEntry(entry, false);
}

/** Load a manifest: from the offline cache first, else the network. */
export async function loadManifest(entry) {
  const href = manifestUrl(entry);
  const cached = hasCaches() && (await (await openCache()).match(href));
  const manifest = cached ? await cached.json() : await fetchJson(href).catch(() => {
    throw new Error(`Can't reach “${entry.name}”. Download it while you have signal.`);
  });
  validateManifest(manifest);
  return { manifest, href };
}

export function validateManifest(m) {
  const problems = [];
  if (m.format !== FORMAT) problems.push(`unsupported format ${m.format}`);
  if (!Array.isArray(m.stops) || !m.stops.length) problems.push('no stops');
  if (!Array.isArray(m.assets)) problems.push('no asset list');
  for (const s of m.stops ?? []) {
    if (!s.trigger || !(s.trigger.radius > 0)) problems.push(`${s.id}: bad trigger`);
    if (!s.episode?.segments?.length) problems.push(`${s.id}: empty episode`);
  }
  if (problems.length) throw new Error(`Invalid tour: ${problems.join('; ')}`);
  return true;
}
