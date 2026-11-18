/**
 * Service Worker —— 网络优先（Network First）
 *
 * 之前是"缓存优先"，导致改了 HTML/CSS/JS 后浏览器一直用旧版本，
 * 除非手动改 CACHE_NAME。这里改成：始终请求网络，成功后顺手更新缓存。
 *
 * 页面导航（mode === 'navigate'）例外：绝不回退到缓存里的旧首页。
 * 否则后端进程一退出，浏览器还能从缓存里"打开"页面并弹出登录框，
 * 用户以为服务在跑，点登录却只得到一句含糊的"网络错误"。
 * 现在导航失败时直接返回一张"服务器未启动"页，并自动重试。
 */
const CACHE_NAME = 'yuxia-chat-v4';
const PRECACHE = [
  '/static/css/style.css',
  '/static/js/api_key.js',
  '/static/js/app.js',
  '/static/manifest.json',
  '/static/image/icon-192.png',
  '/static/image/icon-512.png',
];

const OFFLINE_PAGE = `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>服务器未启动</title>
<style>
  html,body{height:100%;margin:0}
  body{display:flex;align-items:center;justify-content:center;background:#f0f4f8;
       font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;color:#1a202c}
  .box{max-width:360px;padding:32px 28px;background:#fff;border-radius:20px;text-align:center;
       box-shadow:0 16px 48px rgba(0,0,0,.12)}
  h1{font-size:17px;margin:0 0 12px;color:#6366f1}
  p{font-size:13px;line-height:1.8;color:#4a5568;margin:0 0 8px}
  code{background:#e0e7ff;color:#4f46e5;padding:1px 6px;border-radius:4px}
  .dot{display:inline-block;width:6px;height:6px;border-radius:50%;background:#6366f1;
       margin-right:6px;animation:b 1s infinite alternate}
  @keyframes b{from{opacity:.2}to{opacity:1}}
</style></head>
<body><div class="box">
  <h1>连不上本地服务器</h1>
  <p>后端进程可能已经退出。</p>
  <p>在项目目录重新运行 <code>python app.py</code></p>
  <p><span class="dot"></span>本页每 3 秒自动重试，无需手动刷新</p>
</div>
<script>setTimeout(() => location.replace(location.pathname + location.search), 3000);</script>
</body></html>`;

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
      .catch(() => {
        // 页面导航：宁可说"服务器没起来"，也不要拿缓存里的旧首页冒充正常页面
        if (request.mode === 'navigate') {
          return new Response(OFFLINE_PAGE, {
            status: 503,
            headers: { 'Content-Type': 'text/html; charset=utf-8' },
          });
        }
        return caches.match(request).then((cached) => cached || Response.error());
      })
  );
});
