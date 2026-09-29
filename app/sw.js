// Service worker: makes the app open with no connection, serves downloaded tours
// (manifest + audio + optional offline map) from the cache, and caches map tiles you've seen.

const SHELL_CACHE = 'shell-v5';
const TOUR_CACHE = 'cairn-tours-v1'; // written by js/packs.js
const TILE_CACHE = 'map-tiles-v1';
const TILE_HOSTS = ['tiles.openfreemap.org'];
const MAX_TILES = 3000;

const SHELL = [
  './',
  'index.html',
  'css/styles.css',
  'js/app.js',
  'js/api.js',
  'js/drive.js',
  'js/engine.js',
  'js/geo.js',
  'js/location.js',
  'js/map.js',
  'js/packs.js',
  'js/player.js',
  'js/speech.js',
  'js/tour.js',
  'vendor/maplibre/maplibre-gl.mjs',
  'vendor/maplibre/maplibre-gl-shared.mjs',
  'vendor/maplibre/maplibre-gl-worker.mjs',
  'vendor/maplibre/maplibre-gl.css',
  'vendor/pmtiles/pmtiles.js',
  'manifest.webmanifest',
  'icons/icon.svg',
  'icons/icon-192.png',
  'icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(SHELL_CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith('shell-') && k !== SHELL_CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);

  // Engine API: always live (tour creation needs the server).
  if (url.pathname.includes('/v1/')) return;

  // Anything that belongs to a downloaded tour (any origin): cache first, with Range support.
  event.respondWith(
    (async () => {
      const tours = await caches.open(TOUR_CACHE);
      const hit = await tours.match(request.url);
      if (hit) return request.headers.has('range') ? rangeResponse(hit, request.headers.get('range')) : hit;

      if (TILE_HOSTS.includes(url.hostname)) return tile(request);
      if (url.origin !== self.location.origin) return fetch(request);
      return shell(request);
    })(),
  );
});

async function shell(request) {
  // Stale-while-revalidate for the app itself.
  const cache = await caches.open(SHELL_CACHE);
  const cached = await cache.match(request, { ignoreSearch: true });
  const network = fetch(request)
    .then((res) => {
      if (res.ok && res.type === 'basic' && !new URL(request.url).pathname.includes('/tours/')) cache.put(request, res.clone());
      return res;
    })
    .catch(() => cached ?? (request.mode === 'navigate' ? cache.match('index.html') : Response.error()));
  return cached ?? network;
}

async function tile(request) {
  const cache = await caches.open(TILE_CACHE);
  const cached = await cache.match(request);
  if (cached) return cached;
  const res = await fetch(request);
  if (res.ok) {
    cache.put(request, res.clone());
    trimTiles(cache);
  }
  return res;
}

let trimming = false;
async function trimTiles(cache) {
  if (trimming) return;
  trimming = true;
  const keys = await cache.keys();
  await Promise.all(keys.slice(0, Math.max(0, keys.length - MAX_TILES)).map((k) => cache.delete(k)));
  trimming = false;
}

// PMTiles basemaps are read with HTTP Range requests; answer them from the cached file.
async function rangeResponse(response, header) {
  const m = /bytes=(\d+)-(\d*)/.exec(header);
  const blob = await response.blob();
  if (!m) return new Response(blob, { status: 200, headers: response.headers });
  const start = Number(m[1]);
  const end = m[2] ? Math.min(Number(m[2]), blob.size - 1) : blob.size - 1;
  return new Response(blob.slice(start, end + 1), {
    status: 206,
    headers: {
      'Content-Type': response.headers.get('Content-Type') ?? 'application/octet-stream',
      'Content-Range': `bytes ${start}-${end}/${blob.size}`,
      'Content-Length': String(end - start + 1),
    },
  });
}
