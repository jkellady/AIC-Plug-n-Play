/* Service worker: caches only the static shell. Never API responses, never reports. */
'use strict';
const CACHE = 'aic-shell-v2';
const SHELL = ['./', 'index.html', 'app.css', 'app.js', 'manifest.webmanifest',
  'icons/icon-192.png', 'icons/icon-512.png', 'icons/favicon.png'];
const shellUrls = new Set(SHELL.map((p) => new URL(p, self.location).pathname));

const OFFLINE_HTML = '<!doctype html><html lang="en"><head><meta charset="utf-8">' +
  '<meta name="viewport" content="width=device-width, initial-scale=1">' +
  '<title>HR Attendance: not running</title>' +
  '<link rel="stylesheet" href="app.css"></head><body><main class="offline">' +
  '<h1>The Client server is not running</h1>' +
  '<p>This app talks to a small program on this computer, and it is switched off right now.</p>' +
  '<p>Please start HR Attendance from the desktop icon, wait a few seconds, then reload this page.</p>' +
  '</main></body></html>';

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  // Guard: anything under the API is passed straight to the network and never stored.
  if (url.pathname.startsWith('/api')) return;

  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req).catch(() => new Response(OFFLINE_HTML, {
        status: 503, headers: { 'Content-Type': 'text/html; charset=utf-8' }
      }))
    );
    return;
  }
  if (!shellUrls.has(url.pathname)) return;
  event.respondWith(
    caches.open(CACHE).then((cache) =>
      cache.match(req, { ignoreSearch: true }).then((hit) => {
        const net = fetch(req).then((res) => {
          if (res.ok) cache.put(req, res.clone());
          return res;
        }).catch(() => hit || Response.error());
        return net;   // network first so a new app.js shows at once; the cache is only the offline fallback
      })
    ).catch(() => fetch(req))
  );
});
