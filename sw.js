// Frame Log service worker — cache-first, fully offline after first visit.
// Bump CACHE_VERSION whenever you change any file, so installed phones pick it up.
const CACHE_VERSION = "framelog-v12";
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
  const url = new URL(event.request.url);
  // The sync API (api/ next to this file, when a Frame Log server serves the app)
  // always goes to the network: never answered from or stored in the cache.
  // Neither is anything that isn't a GET.
  const apiBase = new URL("api/", self.registration.scope).pathname;
  if (event.request.method !== "GET" ||
      (url.origin === self.location.origin && url.pathname.startsWith(apiBase))) return;

  // Cache-first: serve from cache, fall back to network (and cache the result).
  event.respondWith(
    caches.match(event.request, { ignoreSearch: true }).then((cached) => {
      if (cached) return cached;
      return fetch(event.request).then((resp) => {
        if (resp.ok) {
          const copy = resp.clone();
          caches.open(CACHE_VERSION).then((c) => c.put(event.request, copy));
        }
        return resp;
      });
    })
  );
});
