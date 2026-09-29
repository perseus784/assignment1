// Tour packs are plain JSON files. "Downloading" one stores it in the Cache API so it is
// available with no connection (the service worker also serves it from there).

export const PACK_CACHE = 'tour-packs-v1';
const INDEX_URL = 'packs/index.json';

const hasCaches = () => typeof caches !== 'undefined';

export async function fetchIndex() {
  try {
    const res = await fetch(INDEX_URL, { cache: 'no-cache' });
    if (!res.ok) throw new Error(res.statusText);
    const index = await res.json();
    if (hasCaches()) (await caches.open(PACK_CACHE)).put(INDEX_URL, new Response(JSON.stringify(index)));
    return index;
  } catch (err) {
    // Offline: fall back to the last index we saw.
    const cached = hasCaches() && (await caches.match(INDEX_URL));
    if (cached) return cached.json();
    throw err;
  }
}

export async function isDownloaded(entry) {
  return hasCaches() && !!(await (await caches.open(PACK_CACHE)).match(entry.url));
}

export async function downloadPack(entry) {
  const res = await fetch(entry.url, { cache: 'reload' });
  if (!res.ok) throw new Error(`Download failed (${res.status})`);
  const pack = await res.clone().json();
  validatePack(pack);
  if (hasCaches()) await (await caches.open(PACK_CACHE)).put(entry.url, res);
  return pack;
}

export async function removePack(entry) {
  if (hasCaches()) await (await caches.open(PACK_CACHE)).delete(entry.url);
}

export async function loadPack(entry) {
  const cached = hasCaches() && (await (await caches.open(PACK_CACHE)).match(entry.url));
  if (cached) return cached.json();
  const res = await fetch(entry.url);
  if (!res.ok) throw new Error(`Could not load ${entry.name}. Download it while you have signal.`);
  const pack = await res.json();
  validatePack(pack);
  return pack;
}

/** Throws a descriptive error if a pack is malformed. Shared with the test suite. */
export function validatePack(pack) {
  const problems = [];
  if (!pack.id) problems.push('missing id');
  if (!pack.name) problems.push('missing name');
  if (!Array.isArray(pack.pois) || pack.pois.length === 0) problems.push('pois must be a non-empty array');
  const ids = new Set();
  for (const [i, p] of (pack.pois ?? []).entries()) {
    const where = `pois[${i}] (${p.id ?? '?'})`;
    if (!p.id) problems.push(`${where}: missing id`);
    if (ids.has(p.id)) problems.push(`${where}: duplicate id`);
    ids.add(p.id);
    if (typeof p.lat !== 'number' || Math.abs(p.lat) > 90) problems.push(`${where}: bad lat`);
    if (typeof p.lon !== 'number' || Math.abs(p.lon) > 180) problems.push(`${where}: bad lon`);
    if (!(p.radius > 0)) problems.push(`${where}: radius must be > 0`);
    if (!p.story) problems.push(`${where}: missing story`);
  }
  for (const [i, a] of (pack.ambient ?? []).entries()) {
    if (!a.id || !a.story) problems.push(`ambient[${i}]: needs id and story`);
    if (ids.has(a.id)) problems.push(`ambient[${i}]: id collides with a poi`);
  }
  if (problems.length) throw new Error(`Invalid tour pack: ${problems.join('; ')}`);
  return true;
}
