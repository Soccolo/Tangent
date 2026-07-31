"use strict";

/* A deliberately small PWA shell. API responses are never cached: activity
   logs, profiles and lessons stay behind the live authenticated API. */
const VERSION = "__ASSET_VERSION__";
const CACHE = `tangent-shell-${VERSION}`;
const SHELL = [
  "/",
  `/static/styles.css?v=${VERSION}`,
  `/static/owl.svg?v=${VERSION}`,
  `/static/owl.js?v=${VERSION}`,
  `/static/capture.js?v=${VERSION}`,
  `/static/app.js?v=${VERSION}`,
  `/static/celebrate.js?v=${VERSION}`,
  `/static/manifest.webmanifest?v=${VERSION}`,
];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;

  // Network-first avoids holding an old app across deployments; the cache is
  // only the fallback for a lost connection.
  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response.ok) {
          const copy = response.clone();
          caches.open(CACHE).then((cache) => cache.put(request, copy));
        }
        return response;
      })
      .catch(async () => {
        const hit = await caches.match(request);
        if (hit) return hit;
        if (request.mode === "navigate") return caches.match("/");
        return Response.error();
      })
  );
});
