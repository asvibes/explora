/* Explora phone: service worker.
 *
 * One job: keep the capture-queue page openable with no connection, so photos
 * can be added to the queue on a walk. It never touches /api/ (private data,
 * never cached) and never handles uploads: sending photos home is a deliberate
 * tap in the page, not something that happens in the background.
 *
 * Needs a secure context: https, or http://localhost (for example over USB with
 * `adb reverse tcp:8000 tcp:8000`). On plain http://<laptop-ip> browsers refuse
 * to register service workers, and the queue then works only while the page is open.
 */
"use strict";

const VERSION = "explora-phone-v2";   // bump when any file below changes, so old caches are dropped
const SHELL = [
  "/phone/",
  "/phone/index.html",
  "/phone/capture-queue.js",
  "/phone/manifest.json",
  "/phone/icons/icon-192.png",
  "/phone/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(VERSION).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;                        // uploads etc. go straight to the network
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;         // nothing leaves for other hosts
  if (!url.pathname.startsWith("/phone")) return;          // /api/ and the rest are never cached
  event.respondWith(shellResponse(req, url));
});

async function shellResponse(req, url) {
  const cache = await caches.open(VERSION);
  const key = url.pathname === "/phone" ? "/phone/" : req.url;
  const cached = await cache.match(key, { ignoreSearch: true });

  // Refresh quietly for next time; the cached copy is shown now (works offline).
  const refresh = fetch(req, { cache: "no-cache" })
    .then((res) => {
      if (res && res.ok && res.type === "basic") cache.put(key, res.clone());
      return res;
    })
    .catch(() => null);

  if (cached) return cached;
  const res = await refresh;
  if (res) return res;
  if (req.mode === "navigate") {
    const home = await cache.match("/phone/");
    if (home) return home;
  }
  return new Response("Explora is offline and this page was not saved yet.", {
    status: 503, headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
}