/**
 * Service Worker —— 网络优先（Network First）
 *
 * 之前是"缓存优先"，导致改了 HTML/CSS/JS 后浏览器一直用旧版本，
 * 除非手动改 CACHE_NAME。这里改成：始终请求网络，成功后顺手更新缓存，
 * 只有断网时才回退到缓存。API 请求完全不拦截。
 */
const CACHE_NAME = 'yuxia-chat-v3';
const PRECACHE = [
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
      .then((cache) => cache.addAll(PRECACHE))
      .catch(() => null)
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const { request } = event;

  // 只处理 GET，API 与 SSE 一律交给浏览器
  if (request.method !== 'GET' || request.url.includes('/api/')) return;

  event.respondWith(
    fetch(request)
      .then((response) => {
        // 成功就顺带刷新缓存，供断网时兜底
        if (response && response.ok && response.type === 'basic') {
          const copy = response.clone();
          caches.open(CACHE_NAME)
            .then((cache) => cache.put(request, copy))
            .catch(() => null);
        }
        return response;
      })
      .catch(() => caches.match(request).then((cached) => cached || Response.error()))
  );
});
