/* ═══════════════════════════════════════════════════════════════
   MedWatch Service Worker  –  sw.js
   Handles background Web Push events and shows native OS notifications.
   Served from /static/sw.js but registered at scope /
════════════════════════════════════════════════════════════════ */

const CACHE_NAME = 'medwatch-v1';

// ── Install ───────────────────────────────────────────────────────────────────
self.addEventListener('install', (event) => {
  self.skipWaiting();
});

// ── Activate ──────────────────────────────────────────────────────────────────
self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

// ── Push Event ────────────────────────────────────────────────────────────────
self.addEventListener('push', (event) => {
  let data = {};

  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { title: 'MedWatch Alert', body: event.data ? event.data.text() : 'New alert' };
  }

  const title   = data.title   || '🏥 MedWatch Alert';
  const body    = data.body    || 'A patient vital has exceeded safe limits.';
  const level   = data.level   || 'warning';
  const tag     = data.tag     || 'medwatch-alert';
  const url     = data.url     || '/mobile';

  // Choose badge colour & icon based on severity
  const isCritical = level === 'critical';

  const options = {
    body:              body,
    tag:               tag,            // collapses duplicates (same tag = replace old notification)
    renotify:          true,           // vibrate/sound even if same tag
    requireInteraction: isCritical,   // critical stays until dismissed; warning auto-hides
    icon:              '/static/icons/icon-192.png',
    badge:             '/static/icons/badge-72.png',
    vibrate:           isCritical ? [200, 100, 200, 100, 400] : [200, 100, 200],
    silent:            false,
    data: {
      url:     url,
      level:   level,
      patient: data.patient || '',
      vital:   data.vital   || '',
      value:   data.value   || '',
    },
    actions: [
      { action: 'view',    title: '📋 View Dashboard' },
      { action: 'dismiss', title: '✕ Dismiss'         },
    ],
  };

  event.waitUntil(
    self.registration.showNotification(title, options)
  );
});

// ── Notification Click ────────────────────────────────────────────────────────
self.addEventListener('notificationclick', (event) => {
  event.notification.close();

  if (event.action === 'dismiss') return;

  const targetUrl = event.notification.data?.url || '/mobile';

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true })
      .then((clients) => {
        // Focus existing tab if open
        for (const client of clients) {
          if (client.url.includes('/mobile') || client.url.includes('/dashboard')) {
            return client.focus();
          }
        }
        // Otherwise open a new tab
        return self.clients.openWindow(targetUrl);
      })
  );
});

// ── Push Subscription Change ──────────────────────────────────────────────────
// If the browser rotates the push subscription automatically, re-register it
self.addEventListener('pushsubscriptionchange', (event) => {
  event.waitUntil(
    self.registration.pushManager.subscribe({
      userVisibleOnly:      true,
      applicationServerKey: event.oldSubscription?.options?.applicationServerKey,
    }).then((newSub) => {
      // Notify the server (best-effort)
      return fetch('/api/push/subscribe', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ subscription: newSub.toJSON(), nurse_id: 'unknown' }),
      });
    })
  );
});
