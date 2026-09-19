/**
 * Degradation-event reporter: fire-and-forget telemetry for every user-visible
 * degraded-service surface (connectivity banner, "temporarily unavailable"
 * lyrics failure, slow/failed waveform). POSTs to /api/client-events, which
 * persists to Cloud Logging + Firestore so frequency can be reviewed later.
 *
 * - Never throws; failures are swallowed (telemetry must not cause UX noise).
 * - Per-type throttle so a flapping status can't spam the API.
 * - No-ops on localhost (would otherwise report dev blips to prod).
 *
 * NOTE: intentionally does NOT import from lib/api.ts — api.ts imports
 * backend-status.ts, and this module is called from banner-adjacent code, so
 * importing api.ts here risks a module cycle. The base URL is derived the same
 * way api.ts derives it.
 */

export type DegradationEventType =
  | 'banner_reconnecting'
  | 'banner_unavailable'
  | 'lyrics_load_failed'
  | 'waveform_slow'
  | 'waveform_failed'

/** Minimum gap between two reports of the same event type. */
const THROTTLE_MS = 60_000
const lastSentAt = new Map<DegradationEventType, number>()

export function __resetDegradationEventsForTest() {
  lastSentAt.clear()
}

function apiBaseUrl(): string {
  if (typeof window === 'undefined') return ''
  const h = window.location.hostname
  if (h === 'localhost' || h === '127.0.0.1') return '' // guarded below anyway
  return process.env.NEXT_PUBLIC_API_URL || 'https://api.nomadkaraoke.com'
}

function isLocalhost(): boolean {
  if (typeof window === 'undefined') return true
  const h = window.location.hostname
  return h === 'localhost' || h === '127.0.0.1' || h === '[::1]' || h === '::1'
}

/** Best-effort job id from review-style URLs (/app/jobs#/{id}/… or /jobs/{id}). */
export function jobIdFromLocation(href: string): string | null {
  try {
    const u = new URL(href)
    const hashMatch = u.hash.match(/^#\/([0-9a-f-]{6,64})(\/|$)/i)
    if (hashMatch) return hashMatch[1]
    const pathMatch = u.pathname.match(/\/jobs\/([0-9a-f-]{6,64})(\/|$)/i)
    if (pathMatch) return pathMatch[1]
    return null
  } catch {
    return null
  }
}

function sanitizedHref(): string {
  try {
    const u = new URL(window.location.href)
    u.search = ''
    return u.toString().slice(0, 2048)
  } catch {
    return ''
  }
}

/**
 * Report one degradation event. Safe to call from anywhere client-side;
 * throttled per type, silent on failure.
 */
export function reportDegradationEvent(
  type: DegradationEventType,
  detail?: Record<string, unknown>,
): void {
  try {
    if (typeof window === 'undefined' || isLocalhost()) return

    const now = Date.now()
    const last = lastSentAt.get(type)
    if (last && now - last < THROTTLE_MS) return
    lastSentAt.set(type, now)

    const body = {
      type,
      url: sanitizedHref(),
      job_id: jobIdFromLocation(window.location.href),
      user_email: null as string | null,
      locale: document.documentElement.lang || 'en',
      release: process.env.NEXT_PUBLIC_COMMIT_SHA || '',
      detail: detail ?? null,
    }

    void fetch(`${apiBaseUrl()}/api/client-events`, {
      method: 'POST',
      keepalive: true,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).catch(() => {
      /* swallow — telemetry must never surface */
    })
  } catch {
    /* swallow */
  }
}
