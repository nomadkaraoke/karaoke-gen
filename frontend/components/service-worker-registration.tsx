'use client';

import { useEffect } from 'react';

/**
 * Registers the service worker for push notifications.
 * This component renders nothing - it just handles SW registration on mount.
 */
export function ServiceWorkerRegistration() {
  useEffect(() => {
    if (typeof window === 'undefined') return;

    // Only register in production or when explicitly enabled
    // Service workers require HTTPS (except localhost)
    const isLocalhost = window.location.hostname === 'localhost';
    const isSecure = window.location.protocol === 'https:';

    if (!isSecure && !isLocalhost) {
      console.log('[SW] Skipping service worker registration: requires HTTPS');
      return;
    }

    if (!('serviceWorker' in navigator)) {
      console.log('[SW] Service workers not supported');
      return;
    }

    // Register the service worker
    navigator.serviceWorker
      .register('/sw.js', { scope: '/' })
      .then((registration) => {
        console.log('[SW] Service worker registered:', registration.scope);

        // Check for updates periodically
        registration.addEventListener('updatefound', () => {
          const newWorker = registration.installing;
          if (newWorker) {
            console.log('[SW] New service worker installing...');
            newWorker.addEventListener('statechange', () => {
              if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
                console.log('[SW] New service worker installed, refresh for updates');
              }
            });
          }
        });
      })
      .catch((error) => {
        console.error('[SW] Service worker registration failed:', error);
      });
  }, []);

  // This component renders nothing
  return null;
}
