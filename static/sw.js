const CACHE_NAME = 'yuxia-chat-v2';
const STATIC_ASSETS = [
  '/',
  '/static/css/style.css',
  '/static/js/api_key.js',
  '/static/js/app.js',
  '/static/manifest.json',
  '/static/image/icon-192.png',
  '/static/image/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(STATIC_ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
    )).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  // 仅缓存静态资源，API 请求始终走网络
  if (event.request.url.includes('/api/')) {
    return;
  }
  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});
