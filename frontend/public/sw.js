/*
 * Natya PWA service worker.
 *
 * Deliberately conservative for an authenticated LMS:
 *  - Only same-origin GET requests are handled. The Django API, media, S3/
 *    CloudFront videos and Zoom are other origins and always go straight to
 *    the network -- nothing personal is ever cached here.
 *  - Page navigations are network-only; when the network fails the cached,
 *    self-contained /offline.html is shown. App HTML is never served from
 *    cache, so a deploy can never leave users on a stale page pointing at old
 *    JS chunks.
 *  - /_next/static (content-hashed, immutable) and the app icons are cached
 *    cache-first so the app shell loads fast after the first visit.
 */
const VERSION = "natya-v1";
const STATIC_CACHE = `${VERSION}-static`;
const OFFLINE_URL = "/offline.html";
const PRECACHE = [OFFLINE_URL, "/icon-192.png", "/icon-512.png", "/apple-touch-icon.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(STATIC_CACHE)
      .then((cache) => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function isCacheableStatic(url) {
  return (
    url.pathname.startsWith("/_next/static/") ||
    url.pathname.startsWith("/icon-") ||
    url.pathname === "/apple-touch-icon.png" ||
    url.pathname === "/favicon.ico"
  );
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(() =>
        caches.match(OFFLINE_URL).then((cached) => cached || Response.error())
      )
    );
    return;
  }

  if (isCacheableStatic(url)) {
    event.respondWith(
      caches.match(request).then(
        (cached) =>
          cached ||
          fetch(request).then((response) => {
            if (response.ok && response.type === "basic") {
              const copy = response.clone();
              caches.open(STATIC_CACHE).then((cache) => cache.put(request, copy));
            }
            return response;
          })
      )
    );
  }
  // Everything else: default browser behaviour (network).
});
