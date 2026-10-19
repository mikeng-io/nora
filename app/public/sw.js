/*
 * Nora portal service worker. Hand-written, no build step, no dependencies.
 *
 * What it keeps, and why:
 *   pages   the HTML of each portal page opened while online (network first,
 *           4s, then this copy, then /offline.html), so a page opened before
 *           still opens with no signal. Only a plain 200 text/html that was
 *           not redirected is kept: a session that expired redirects to the
 *           login page, and that must never be stored under /map.
 *   static  hashed build output, the MapLibre worker and the icons: never
 *           change under one URL, so cache first.
 *   runtime resized images and the manifest: stale while revalidate, capped.
 *   tiles   OpenFreeMap tiles, glyphs and tilejson: stale while revalidate,
 *           capped at ~800 entries, so a map seen recently draws offline.
 *
 * What it never touches: /api/* (data offline is IndexedDB's job, via the
 * persisted TanStack Query cache, which knows how old each answer is; a
 * cached API response would present an old answer as a current one), React
 * Server Component fetches (RSC: 1 / ?_rsc), Auth.js routes, the login page,
 * and this file.
 *
 * Updating: bump VERSION on any change to this file or to what it precaches.
 * The browser byte-compares sw.js on each visit (next.config.ts serves it
 * no-cache), installs the new worker, and it WAITS; the portal offers a
 * reload (src/components/providers/sw-register.tsx), which posts
 * "skip-waiting". Activation then deletes every cache of an older VERSION.
 * TILES_VERSION is separate so an app update keeps the map tiles.
 *
 * No navigation preload, on purpose. With preload on, every navigation
 * this worker lets pass (the /api/auth/callback/discord redirect among
 * them) reaches the server twice: the preload, then the browser's own
 * request when no response is given. Discord's one-time code does not
 * survive a second use, so sign-in would fail at random.
 *
 * Whose pages: a stored page carries the person it was rendered for. The
 * login page wipes every cache (src/app/[locale]/login/forget-device.tsx),
 * and every path to a new session passes through it, so nobody signs in
 * on a device and is handed the last person's pages.
 */

const VERSION = "v2-2026-10-20";
const TILES_VERSION = "t1";

const PAGES = `nora-pages-${VERSION}`;
const STATIC = `nora-static-${VERSION}`;
const RUNTIME = `nora-runtime-${VERSION}`;
const TILES = `nora-tiles-${TILES_VERSION}`;
const OWNED = [PAGES, STATIC, RUNTIME, TILES];

const OFFLINE_URL = "/offline.html";
const PRECACHE = [
  OFFLINE_URL,
  "/manifest.webmanifest",
  "/favicon.ico",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/icons/icon-maskable-512.png",
  "/icons/apple-touch-icon.png",
];

const NAV_TIMEOUT_MS = 4000;
const CAPS = { [PAGES]: 60, [RUNTIME]: 150, [TILES]: 800 };

// ------------------------------------------------------------- lifecycle

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(STATIC).then((cache) => cache.addAll(PRECACHE.map((u) => new Request(u, { cache: "reload" })))),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(keys.filter((k) => k.startsWith("nora-") && !OWNED.includes(k)).map((k) => caches.delete(k)));
      // Off, and turned off if an earlier build ever turned it on (the header
      // says why): the setting outlives the worker that set it.
      if (self.registration.navigationPreload) await self.registration.navigationPreload.disable();
      await self.clients.claim();
    })(),
  );
});

self.addEventListener("message", (event) => {
  if (event.data === "skip-waiting") {
    self.skipWaiting();
  } else if (event.data === "clear-caches") {
    // Signing out forgets every page this device kept.
    event.waitUntil(
      caches.keys().then((keys) => Promise.all(keys.filter((k) => k.startsWith("nora-")).map((k) => caches.delete(k)))),
    );
  }
});

// ----------------------------------------------------------------- fetch

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);

  if (url.origin === "https://tiles.openfreemap.org") {
    event.respondWith(staleWhileRevalidate(event, TILES));
    return;
  }
  if (url.origin !== self.location.origin) return;

  const path = url.pathname;
  if (
    path.startsWith("/api/") ||
    path === "/sw.js" ||
    request.headers.get("RSC") === "1" ||
    url.searchParams.has("_rsc") ||
    request.headers.has("Next-Router-State-Tree")
  ) {
    return;
  }

  if (request.mode === "navigate") {
    event.respondWith(navigate(event, url));
    return;
  }
  if (
    path.startsWith("/_next/static/") ||
    path.startsWith("/vendor/maplibre/") ||
    path.startsWith("/icons/") ||
    path === OFFLINE_URL
  ) {
    event.respondWith(cacheFirst(request, STATIC));
    return;
  }
  if (
    path.startsWith("/_next/image") ||
    path === "/manifest.webmanifest" ||
    path === "/favicon.ico" ||
    /\.(?:woff2?|ttf|otf)$/.test(path)
  ) {
    event.respondWith(staleWhileRevalidate(event, RUNTIME));
  }
});

// ------------------------------------------------------------- strategies

const isLogin = (path) => /^\/(?:zh-Hant|en)\/login(?:\/|$)/.test(path);

function keepable(response) {
  return (
    response &&
    response.status === 200 &&
    !response.redirected &&
    response.type === "basic" &&
    (response.headers.get("content-type") || "").includes("text/html")
  );
}

async function navigate(event, url) {
  const { request } = event;
  const cache = await caches.open(PAGES);
  const network = (async () => {
    const response = await fetch(request);
    if (keepable(response) && !isLogin(url.pathname)) {
      await cache.put(request, response.clone());
      trim(PAGES);
    }
    return response;
  })();
  // Keep the network read alive past the timeout, so a slow answer still
  // refreshes the stored copy for next time.
  event.waitUntil(network.catch(() => undefined));

  const fallback = async () =>
    (await cache.match(request)) ||
    (await cache.match(request, { ignoreSearch: true })) ||
    (await caches.match(OFFLINE_URL));

  let timer;
  const timeout = new Promise((resolve) => {
    timer = setTimeout(() => resolve("timeout"), NAV_TIMEOUT_MS);
  });
  try {
    const winner = await Promise.race([network, timeout]);
    if (winner !== "timeout") return winner;
    // Slow, not failed: answer from the stored copy if there is one, else
    // keep waiting for the network rather than calling a slow link offline.
    const stored = (await cache.match(request)) || (await cache.match(request, { ignoreSearch: true }));
    return stored || (await network);
  } catch {
    return (await fallback()) || Response.error();
  } finally {
    clearTimeout(timer);
  }
}

async function cacheFirst(request, name) {
  const cache = await caches.open(name);
  const hit = await cache.match(request);
  if (hit) return hit;
  const response = await fetch(request);
  if (response.ok && response.type === "basic") await cache.put(request, response.clone());
  return response;
}

async function staleWhileRevalidate(event, name) {
  const { request } = event;
  const cache = await caches.open(name);
  const hit = await cache.match(request);
  const update = fetch(request)
    .then(async (response) => {
      // Tiles are cross-origin CORS reads ("cors" type); an opaque answer
      // cannot be checked for success and is never kept.
      if (response.ok && (response.type === "basic" || response.type === "cors")) {
        await cache.put(request, response.clone());
        trim(name);
      }
      return response;
    })
    .catch(() => undefined);
  if (hit) {
    event.waitUntil(update);
    return hit;
  }
  return (await update) || Response.error();
}

// Oldest-first eviction (Cache keys come back in insertion order), checked
// every few writes rather than on each one.
const writes = {};
async function trim(name) {
  writes[name] = (writes[name] || 0) + 1;
  if (writes[name] % 20 !== 0) return;
  const cap = CAPS[name];
  if (!cap) return;
  const cache = await caches.open(name);
  const keys = await cache.keys();
  for (let i = 0; i < keys.length - cap; i++) await cache.delete(keys[i]);
}
