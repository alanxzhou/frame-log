// Frame Log service worker — cache-first, fully offline after first visit.
// Bump CACHE_VERSION whenever you change any file, so installed phones pick it up.
const CACHE_VERSION = "framelog-v3";
const ASSETS = [
  ".",
  "index.html",
  "manifest.json",
  "icon-192.png",
  "icon-512.png"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then((cache) => cache.addAll(ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  // Drop old cache versions
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_VERSION).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  // Cache-first: serve from cache, fall back to network (and cache the result).
  event.respondWith(
    caches.match(event.request, { ignoreSearch: true }).then((cached) => {
      if (cached) return cached;
      return fetch(event.request).then((resp) => {
        if (resp.ok && event.request.method === "GET") {
          const copy = resp.clone();
          caches.open(CACHE_VERSION).then((c) => c.put(event.request, copy));
        }
        return resp;
      });
    })
  );
});
