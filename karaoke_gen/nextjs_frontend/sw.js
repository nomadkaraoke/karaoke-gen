/**
 * Service Worker for Web Push Notifications
 *
 * Handles push events and notification clicks for the Karaoke Generator PWA.
 * This service worker is registered by the ServiceWorkerRegistration component.
 */

// Cache name for static assets (versioned for updates)
const CACHE_VERSION = 'v1';
const STATIC_CACHE = `karaoke-gen-static-${CACHE_VERSION}`;

// Assets to cache for offline support
const STATIC_ASSETS = [
  '/nomad-logo.png',
  '/nomad-karaoke-logo.svg',
  '/favicon.ico',
];

/**
 * Install event - cache static assets
 */
self.addEventListener('install', (event) => {
  console.log('[SW] Installing service worker...');
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => {
      return cache.addAll(STATIC_ASSETS);
    }).then(() => {
      console.log('[SW] Static assets cached');
      return self.skipWaiting(); // Activate immediately
    })
  );
});

/**
 * Activate event - clean up old caches
 */
self.addEventListener('activate', (event) => {
  console.log('[SW] Activating service worker...');
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames
          .filter((name) => name.startsWith('karaoke-gen-') && name !== STATIC_CACHE)
          .map((name) => {
            console.log('[SW] Deleting old cache:', name);
            return caches.delete(name);
          })
      );
    }).then(() => {
      console.log('[SW] Service worker activated');
      return self.clients.claim(); // Take control of all pages
    })
  );
});

/**
 * Push event - show notification when push message received
 */
self.addEventListener('push', (event) => {
  console.log('[SW] Push event received');

  let data = {
    title: 'Karaoke Generator',
    body: 'You have a notification',
    icon: '/nomad-logo.png',
    tag: 'default',
    url: '/app/',
  };

  // Parse push data if available
  if (event.data) {
    try {
      const payload = event.data.json();
      data = {
        title: payload.title || data.title,
        body: payload.body || data.body,
        icon: payload.icon || data.icon,
        tag: payload.tag || data.tag,
        url: payload.url || data.url,
      };
    } catch (err) {
      console.error('[SW] Error parsing push data:', err);
      // Try as text
      const text = event.data.text();
      if (text) {
        data.body = text;
      }
    }
  }

  // Show the notification
  const options = {
    body: data.body,
    icon: data.icon,
    badge: '/favicon-32x32.png',
    tag: data.tag, // Replaces notifications with same tag
    renotify: true, // Vibrate/sound even if replacing
    requireInteraction: true, // Don't auto-dismiss
    data: {
      url: data.url,
    },
    // Actions for the notification (optional)
    actions: [
      {
        action: 'open',
        title: 'Open',
      },
      {
        action: 'dismiss',
        title: 'Dismiss',
      },
    ],
  };

  event.waitUntil(
    self.registration.showNotification(data.title, options)
  );
});

/**
 * Notification click event - open the app or focus existing window
 */
self.addEventListener('notificationclick', (event) => {
  console.log('[SW] Notification clicked:', event.action);

  event.notification.close();

  // Handle dismiss action
  if (event.action === 'dismiss') {
    return;
  }

  // Get the URL to open
  const url = event.notification.data?.url || '/app/';
  const fullUrl = new URL(url, self.location.origin).href;

  // Try to focus an existing window or open a new one
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      // Check if there's already a window open
      for (const client of clients) {
        // If we find a matching URL, focus it
        if (client.url === fullUrl && 'focus' in client) {
          return client.focus();
        }
      }

      // Check if there's any window from our origin
      for (const client of clients) {
        if (client.url.startsWith(self.location.origin) && 'focus' in client) {
          // Navigate the existing window and focus it
          return client.navigate(fullUrl).then((client) => client?.focus());
        }
      }

      // No existing window, open a new one
      if (self.clients.openWindow) {
        return self.clients.openWindow(fullUrl);
      }
    })
  );
});

/**
 * Notification close event - cleanup if needed
 */
self.addEventListener('notificationclose', (event) => {
  console.log('[SW] Notification closed');
  // Could track analytics here if desired
});

/**
 * Message event - handle messages from the main app
 */
self.addEventListener('message', (event) => {
  console.log('[SW] Message received:', event.data);

  if (event.data?.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

console.log('[SW] Service worker loaded');
