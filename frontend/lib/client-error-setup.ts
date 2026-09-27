'use client'

import { isBenignError, reportClientError } from '@/lib/crash-reporter'
import { addBreadcrumb, installDiagnostics } from '@/lib/diagnostics'
import { hardReload, isChunkLoadError, isStale, startAmbientVersionPoll } from '@/lib/version-check'

let installed = false

// Known locale prefixes — matches next-intl routing config. Any route whose
// first segment isn't in this set (e.g. /admin, /app) falls through to 'en'.
const KNOWN_LOCALES = new Set([
  'ar', 'ca', 'cs', 'da', 'de', 'el', 'en', 'es', 'fi', 'fr', 'he', 'hi', 'hr',
  'hu', 'id', 'it', 'ja', 'ko', 'nb', 'nl', 'pl', 'pt', 'ro', 'ru', 'sk', 'sv',
  'th', 'tl', 'tr', 'uk', 'vi', 'zh',
])

function detectLocale(): string {
  if (typeof window === 'undefined') return 'en'
  const first = window.location.pathname.split('/').filter(Boolean)[0]
  if (first && KNOWN_LOCALES.has(first)) return first
  return 'en'
}

function buildContext(userEmail: string | null) {
  return {
    href: typeof window !== 'undefined' ? window.location.href : '',
    userAgent: typeof navigator !== 'undefined' ? navigator.userAgent : '',
    innerWidth: typeof window !== 'undefined' ? window.innerWidth : undefined,
    innerHeight: typeof window !== 'undefined' ? window.innerHeight : undefined,
    locale: detectLocale(),
    userEmail,
  }
}

/**
 * Browsers hide errors thrown by cross-origin scripts (third-party tags, in-app
 * browser/extension injections) behind a bare "Script error." with no error
 * object, filename or line. There's nothing actionable in them — the only stack
 * we'd get is our own handler's — so they're alert noise.
 */
export function isOpaqueCrossOriginError(
  event: Pick<ErrorEvent, 'error' | 'message'>
): boolean {
  return event.error == null && /^Script error\.?$/i.test((event.message ?? '').trim())
}

export function installGlobalErrorHandlers(getUserEmail: () => string | null) {
  if (installed) return
  if (typeof window === 'undefined') return
  installed = true
  installDiagnostics()

  const maybeReloadForChunkError = async (err: unknown): Promise<boolean> => {
    if (!isChunkLoadError(err)) return false
    const staleResult = await isStale().catch(() => null)
    // If the bundle is stale OR we can't verify, still reload — ChunkLoadError
    // by itself is strong signal of post-deploy mismatch.
    const triggered = hardReload(staleResult?.latestSha)
    return triggered
  }

  window.addEventListener('error', (event) => {
    if (isOpaqueCrossOriginError(event)) return
    // Some engine-level failures (Firefox "out of memory") arrive with no Error
    // object — or a non-Error value — so there is no real stack to send.
    const synthetic = !(event.error instanceof Error)
    const err = synthetic
      ? new Error(event.message || String(event.error ?? 'Unknown window error'))
      : event.error
    addBreadcrumb('error', (err as Error).message)
    void (async () => {
      const reloaded = await maybeReloadForChunkError(err)
      if (reloaded) return
      void reportClientError({
        error: err,
        source: 'window.onerror',
        context: buildContext(getUserEmail()),
        synthetic,
        extra: {
          filename: event.filename,
          lineno: event.lineno,
          colno: event.colno,
        },
      })
    })()
  })

  window.addEventListener('unhandledrejection', (event) => {
    // Check the raw reason before wrapping so e.g. a media AbortError DOMException
    // is still recognised as benign.
    if (isBenignError(event.reason)) return
    const synthetic = !(event.reason instanceof Error)
    const err = synthetic ? new Error(String(event.reason)) : event.reason
    addBreadcrumb('error', `unhandledrejection: ${(err as Error).message}`)
    void (async () => {
      const reloaded = await maybeReloadForChunkError(err)
      if (reloaded) return
      void reportClientError({
        error: err,
        source: 'unhandledrejection',
        context: buildContext(getUserEmail()),
        synthetic,
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
