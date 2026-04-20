'use client'

import { reportClientError } from '@/lib/crash-reporter'

let installed = false

function buildContext(userEmail: string | null, locale: string) {
  return {
    href: typeof window !== 'undefined' ? window.location.href : '',
    userAgent: typeof navigator !== 'undefined' ? navigator.userAgent : '',
    innerWidth: typeof window !== 'undefined' ? window.innerWidth : undefined,
    innerHeight: typeof window !== 'undefined' ? window.innerHeight : undefined,
    locale,
    userEmail,
  }
}

export function installGlobalErrorHandlers(getUserEmail: () => string | null, locale: string) {
  if (installed) return
  if (typeof window === 'undefined') return
  installed = true

  window.addEventListener('error', (event) => {
    const err = event.error ?? new Error(event.message || 'Unknown window error')
    void reportClientError({
      error: err,
      source: 'window.onerror',
      context: buildContext(getUserEmail(), locale),
      extra: {
        filename: event.filename,
        lineno: event.lineno,
        colno: event.colno,
      },
    })
  })

  window.addEventListener('unhandledrejection', (event) => {
    const err = event.reason instanceof Error ? event.reason : new Error(String(event.reason))
    void reportClientError({
      error: err,
      source: 'unhandledrejection',
      context: buildContext(getUserEmail(), locale),
    })
  })
}
