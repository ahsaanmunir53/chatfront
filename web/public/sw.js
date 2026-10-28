// Chatfront service worker: the app shell works offline; shop data and chats always come
// from the network, so nothing a shop changes is ever served stale. It also shows the shop's
// alerts (feature 115) when the app is closed.
const CACHE = "chatfront-shell-v3";
// Paths are relative to where the app is served ("/" or "/chatfront/").
const BASE = new URL(self.registration.scope).pathname;
const SHELL = [BASE, `${BASE}manifest.webmanifest`, `${BASE}icons/icon-192.png`];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  const path = url.pathname.slice(BASE.length - 1);
  if (/^\/(api|media|webhooks|internal|health)\b/.test(path)) return;

  if (path.startsWith("/assets/")) {
    // Hashed file names never change, so the cache can answer first.
    event.respondWith(
      caches.match(event.request).then(
        (hit) =>
          hit ||
          fetch(event.request).then((response) => {
            const copy = response.clone();
            caches.open(CACHE).then((cache) => cache.put(event.request, copy));
            return response;
          }),
      ),
    );
    return;
  }

  if (event.request.mode === "navigate") {
    event.respondWith(fetch(event.request).catch(() => caches.match(BASE)));
  }
});

// An alert from the shop: a payment to check, a customer who wants a person, low stock.
self.addEventListener("push", (event) => {
  const alert = event.data ? event.data.json() : {};
  const icon = `${BASE}icons/icon-192.png`;
  event.waitUntil(
    self.registration.showNotification(alert.title || "Chatfront", {
      body: alert.body || "",
      lang: alert.lang,
      dir: alert.dir || "auto",
      tag: alert.tag,
      renotify: Boolean(alert.tag),
      icon,
      badge: icon,
      data: { url: alert.url || `${BASE}owner` },
    }),
  );
});

// Tapping the alert opens the console, or brings an open one forward.
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = event.notification.data?.url || `${BASE}owner`;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((open) => {
      const tab = open.find((client) => client.url.startsWith(url));
      return tab ? tab.focus() : self.clients.openWindow(url);
    }),
  );
});
