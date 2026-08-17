// Asset caching only — no offline data story (design spec §3.2: every
// module is a live fetch against Nora/Pulse, and there's nothing meaningful
// to show offline). This exists to make the app installable and fast on
// repeat loads, not to work without a network.
const CACHE_NAME = "nora-static-v1";
const STATIC_ASSETS = ["/manifest.json"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS)));
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  // Never intercept API calls — /api/status and /api/memory must always
  // hit the network; a cached status page would silently go stale.
  if (request.url.includes("/api/")) return;

  event.respondWith(
    caches.match(request).then((cached) => cached ?? fetch(request)),
  );
});
