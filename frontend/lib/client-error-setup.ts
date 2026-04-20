'use client'

import { reportClientError } from '@/lib/crash-reporter'
import { hardReload, isChunkLoadError, isStale, startAmbientVersionPoll } from '@/lib/version-check'

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

  const maybeReloadForChunkError = async (err: unknown): Promise<boolean> => {
    if (!isChunkLoadError(err)) return false
    const staleResult = await isStale().catch(() => null)
    // If the bundle is stale OR we can't verify, still reload — ChunkLoadError
    // by itself is strong signal of post-deploy mismatch.
    const triggered = hardReload(staleResult?.latestSha)
    return triggered
  }

  window.addEventListener('error', (event) => {
    const err = event.error ?? new Error(event.message || 'Unknown window error')
    void (async () => {
      const reloaded = await maybeReloadForChunkError(err)
      if (reloaded) return
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
    })()
  })

  window.addEventListener('unhandledrejection', (event) => {
    const err = event.reason instanceof Error ? event.reason : new Error(String(event.reason))
    void (async () => {
      const reloaded = await maybeReloadForChunkError(err)
      if (reloaded) return
      void reportClientError({
        error: err,
        source: 'unhandledrejection',
        context: buildContext(getUserEmail(), locale),
      })
    })()
  })

  // Ambient poll: every 10 min, if stale, stash info for UI to pick up via
  // a CustomEvent. We do NOT auto-reload here — that's reserved for crashes.
  startAmbientVersionPoll((r) => {
    try {
      sessionStorage.setItem('karaoke_latest_sha', r.latestSha)
      window.dispatchEvent(new CustomEvent('karaoke:stale-version', { detail: r }))
    } catch {
      /* ignore */
    }
  })
}
