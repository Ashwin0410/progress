const CACHE_NAME = 'progress-v2';
const STATIC_ASSETS = ['/', '/static/style.css', '/static/app.js', '/manifest.json'];

self.addEventListener('install', (e) => {
    e.waitUntil(caches.open(CACHE_NAME).then(c => c.addAll(STATIC_ASSETS)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (e) => {
    e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', (e) => {
    if (e.request.method !== 'GET' || e.request.url.includes('/api/')) return;
    e.respondWith(
        fetch(e.request).then(r => {
            if (r.ok) { const c = r.clone(); caches.open(CACHE_NAME).then(cache => cache.put(e.request, c)); }
            return r;
        }).catch(() => caches.match(e.request).then(c => c || new Response('Offline', { status: 503 })))
    );
});
