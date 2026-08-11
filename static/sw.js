/* Music High Res — service worker (PWA shell).
 *
 * Precaches the app shell so it loads instantly offline/on re-open, and
 * cache-first serves the static assets. Never touches /api/, /audio or /art
 * (streams must stay fresh and Range-capable).
 */
// NOTE: bump this cache name on every release — otherwise installed clients
// keep serving the previous shell from cache after a deploy.
const CACHE = "mhr-shell-v3";
const PRECACHE = [
  "./",
  "./index.html",
  "./manifest.json",
  "./icon.svg",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches
      .open(CACHE)
      .then((c) => c.addAll(PRECACHE))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  const p = url.pathname;
  // Never intercept API calls or the audio/art streams.
  if (p.startsWith("/api/") || p === "/api" || p.startsWith("/audio") || p.startsWith("/art")) return;

  e.respondWith(
    caches.match(req).then((hit) => {
      if (hit) return hit;
      return fetch(req).then((res) => {
        // Cache shell assets on the fly (index/manifest/icons only).
        if (res.ok && (p === "/" || p.endsWith("index.html") || p.endsWith(".png") || p.endsWith(".svg") || p.endsWith("manifest.json"))) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
        }
        return res;
      });
    })
  );
});
