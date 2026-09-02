// Minimal service worker — required by Chrome so the site qualifies as an
// installable app ("Add to Home Screen" / install prompt).
self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  // Simple passthrough — just fetch normally from the network.
  event.respondWith(fetch(event.request));
});
