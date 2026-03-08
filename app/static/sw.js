const CACHE_NAME = 'sixmanager-cache-v3';
const ASSETS_TO_CACHE = [
    '/static/manifest.json',
    '/static/img/icon-192.png',
    '/static/img/icon-512.png'
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => cache.addAll(ASSETS_TO_CACHE))
    );
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) =>
            Promise.all(
                keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
            )
        )
    );
    self.clients.claim();
});

async function networkFirst(request, shouldCache = false) {
    try {
        const response = await fetch(request);
        if (shouldCache && response.ok) {
            const cache = await caches.open(CACHE_NAME);
            cache.put(request, response.clone());
        }
        return response;
    } catch {
        const cached = await caches.match(request);
        if (cached) return cached;
        throw new Error('Network and cache unavailable');
    }
}

async function cacheFirst(request) {
    const cached = await caches.match(request);
    if (cached) return cached;

    const response = await fetch(request);
    if (response.ok) {
        const cache = await caches.open(CACHE_NAME);
        cache.put(request, response.clone());
    }
    return response;
}

self.addEventListener('fetch', (event) => {
    if (event.request.method !== 'GET') return;

    const url = new URL(event.request.url);
    const isSameOrigin = url.origin === self.location.origin;
    const isStatic = isSameOrigin && url.pathname.startsWith('/static/');
    const isCssOrJs = isSameOrigin && (
        url.pathname.startsWith('/static/css/') || url.pathname.startsWith('/static/js/')
    );

    // For navigation and CSS/JS, prioritize network to avoid stale app shell/styles.
    if (event.request.mode === 'navigate' || isCssOrJs) {
        event.respondWith(networkFirst(event.request, isStatic));
        return;
    }

    if (isStatic) {
        event.respondWith(cacheFirst(event.request));
    }
});
