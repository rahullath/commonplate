// Minimal service worker — exists to make the site installable as a PWA,
// not to cache the site. This is a live ordering site with real-time
// pricing, availability and order status; caching /menu, /api/*, or
// checkout would risk serving stale prices or broken order state. Only the
// static logo/icon shell is cached — everything else always hits the
// network.
const CACHE = "shell-v1";
const SHELL_ASSETS = [
  "/static/logo.png",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL_ASSETS)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (SHELL_ASSETS.includes(url.pathname)) {
    event.respondWith(caches.match(event.request).then((cached) => cached || fetch(event.request)));
  }
  // Everything else: no event.respondWith() means the browser's default
  // network fetch happens untouched.
});
