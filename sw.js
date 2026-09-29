// Service worker: caches the app shell so the app opens with no connection, and serves
// downloaded tour packs from the pack cache (populated by js/packs.js).

const SHELL_CACHE = 'shell-v1';
const PACK_CACHE = 'tour-packs-v1';
const SHELL = [
  './',
  'index.html',
  'css/styles.css',
  'js/app.js',
  'js/engine.js',
  'js/geo.js',
  'js/location.js',
  'js/narrator.js',
  'js/packs.js',
  'js/radar.js',
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
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith('shell-') && k !== SHELL_CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  if (request.method !== 'GET' || new URL(request.url).origin !== self.location.origin) return;

  if (new URL(request.url).pathname.includes('/packs/')) {
    // Packs are managed explicitly by the app; the network is the source of truth when online.
    event.respondWith(fetch(request).catch(() => caches.open(PACK_CACHE).then((c) => c.match(request)).then((r) => r ?? Response.error())));
    return;
  }

  // App shell: serve from cache, refresh in the background (stale-while-revalidate).
  event.respondWith(
    caches.open(SHELL_CACHE).then(async (cache) => {
      const cached = await cache.match(request, { ignoreSearch: true });
      const network = fetch(request)
        .then((res) => {
          if (res.ok) cache.put(request, res.clone());
          return res;
        })
        .catch(() => cached ?? (request.mode === 'navigate' ? cache.match('index.html') : Response.error()));
      return cached ?? network;
    }),
  );
});
