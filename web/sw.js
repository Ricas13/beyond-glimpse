// Beyond Glimpse service worker: cache the tiny application shell only.
// Catalogue API responses and media artwork deliberately stay out of Cache Storage.

const CACHE_NAME = 'beyond-glimpse-shell-v13';
const SHELL_ASSETS = [
    '/manifest.json',
    '/offline.html',
    '/large-library.js?v=6',
    '/library-browse.js?v=1',
    '/startup-status.js?v=3'
];

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => cache.addAll(SHELL_ASSETS))
            .catch(() => undefined)
            .then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys()
            .then(names => Promise.all(names
                .filter(name => name !== CACHE_NAME)
                .filter(name => name.startsWith('glimpse-media-') || name.startsWith('beyond-glimpse-'))
                .map(name => caches.delete(name))))
            .then(() => self.clients.claim())
    );
});

function isDynamicRequest(request) {
    const url = new URL(request.url);
    return url.pathname.startsWith('/data/') ||
        url.pathname.startsWith('/api/') ||
        url.pathname.startsWith('/poster/');
}

function isHtmlRequest(request) {
    return request.mode === 'navigate' || (request.headers.get('Accept') || '').includes('text/html');
}

async function networkFirst(request) {
    try {
        const response = await fetch(request);
        if (response.ok) {
            const cache = await caches.open(CACHE_NAME);
            cache.put(request, response.clone());
        }
        return response;
    } catch (error) {
        return (await caches.match(request)) || (await caches.match('/offline.html')) || Promise.reject(error);
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

self.addEventListener('fetch', event => {
    if (!event.request.url.startsWith(self.location.origin)) return;

    // The server owns catalogue/detail/poster caching. Never accumulate those
    // responses again in browser Cache Storage.
    if (isDynamicRequest(event.request)) {
        event.respondWith(fetch(event.request));
        return;
    }

    if (isHtmlRequest(event.request)) {
        event.respondWith(networkFirst(event.request));
        return;
    }

    event.respondWith(cacheFirst(event.request));
});

self.addEventListener('message', event => {
    if (!event.data || !['CLEAR_THEMED_CACHE', 'CLEAR_DATA_CACHE', 'CLEAR_ALL_CACHE'].includes(event.data.type)) {
        return;
    }
    event.waitUntil(
        caches.keys()
            .then(names => Promise.all(names.map(name => caches.delete(name))))
            .then(() => event.ports[0]?.postMessage({ success: true }))
    );
});
