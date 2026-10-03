/**
 * Degradation-event reporter: fire-and-forget telemetry for every user-visible
 * degraded-service surface (connectivity banner, "temporarily unavailable"
 * lyrics failure, slow/failed waveform). POSTs to /api/client-events, which
 * persists to Cloud Logging + Firestore so frequency can be reviewed later.
 *
 * - Never throws; failures are swallowed (telemetry must not cause UX noise).
 * - Per-type throttle so a flapping status can't spam the API.
 * - No-ops on localhost (would otherwise report dev blips to prod).
 * - Identity: the access token (if any) goes in the Authorization header and
 *   the server resolves the user from it — the body never carries an email.
 *   A device fingerprint (≤1.5 s wait) and a per-tab id are attached so
 *   anonymous reports can still be correlated.
 * - Episodes: the banner brackets each outage with start/endDegradationEpisode;
 *   every event reported while one is active carries its `episode_id`.
 *
 * NOTE: intentionally does NOT import from lib/api.ts — api.ts imports
 * backend-status.ts, and this module is called from banner-adjacent code, so
 * importing api.ts here risks a module cycle. The base URL is derived the same
 * way api.ts derives it, and the access token is read straight from
 * localStorage (same key api.ts uses).
 */

export type DegradationEventType =
  | 'banner_reconnecting'
  | 'banner_unavailable'
  | 'banner_waking'
  | 'banner_recovered'
  | 'lyrics_load_failed'
  | 'waveform_slow'
  | 'waveform_failed'

/** Minimum gap between two reports of the same event type. */
const THROTTLE_MS = 60_000
/**
 * Types exempt from the per-type throttle. `banner_recovered` is emitted once
 * per episode by the banner (which guarantees that), and dropping it would
 * lose the episode's duration.
 */
const UNTHROTTLED: ReadonlySet<DegradationEventType> = new Set(['banner_recovered'])
/** Max time a report waits for the device fingerprint before sending without it. */
export const FINGERPRINT_WAIT_MS = 1_500
const ACCESS_TOKEN_KEY = 'karaoke_access_token'
const TAB_ID_KEY = 'nk_tab_id'

const lastSentAt = new Map<DegradationEventType, number>()
let currentEpisodeId: string | null = null
let memoryTabId: string | null = null

export function __resetDegradationEventsForTest() {
  lastSentAt.clear()
  currentEpisodeId = null
  memoryTabId = null
}

function randomId(): string {
  try {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID()
    }
  } catch {
    /* fall through */
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`
}

/**
 * Start a degradation episode (idempotent: returns the active id if one is
 * already open). Events reported until endDegradationEpisode() carry this id.
 */
export function startDegradationEpisode(): string {
  if (!currentEpisodeId) currentEpisodeId = randomId()
  return currentEpisodeId
}

/** End the active degradation episode (no-op if none). */
export function endDegradationEpisode(): void {
  currentEpisodeId = null
}

export function getDegradationEpisodeId(): string | null {
  return currentEpisodeId
}

/** Random id stable for the lifetime of this browser tab (sessionStorage). */
export function getTabId(): string {
  try {
    const existing = window.sessionStorage.getItem(TAB_ID_KEY)
    if (existing) return existing
    const id = randomId()
    window.sessionStorage.setItem(TAB_ID_KEY, id)
    return id
  } catch {
    // sessionStorage blocked (privacy mode / sandboxed iframe): per-load id.
    if (!memoryTabId) memoryTabId = randomId()
    return memoryTabId
  }
}

function readAccessToken(): string | null {
  try {
    return window.localStorage.getItem(ACCESS_TOKEN_KEY)
  } catch {
    return null
  }
}

/** Device fingerprint, waiting at most FINGERPRINT_WAIT_MS; null on timeout/failure. */
function fingerprintWithTimeout(): Promise<string | null> {
  return new Promise<string | null>((resolve) => {
    let settled = false
    const done = (v: string | null) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      resolve(v)
    }
    const timer = setTimeout(() => done(null), FINGERPRINT_WAIT_MS)
    try {
      import('./fingerprint')
        .then((m) => m.getDeviceFingerprint())
        .then((fp) => done(fp ?? null), () => done(null))
    } catch {
      done(null)
    }
  })
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
 * throttled per type (except `banner_recovered`), silent on failure. Returns
 * immediately — the send happens asynchronously (after ≤1.5 s fingerprint wait).
 */
export function reportDegradationEvent(
  type: DegradationEventType,
  detail?: Record<string, unknown>,
): void {
  try {
    if (typeof window === 'undefined' || isLocalhost()) return

    if (!UNTHROTTLED.has(type)) {
      const now = Date.now()
      const last = lastSentAt.get(type)
      if (last && now - last < THROTTLE_MS) return
      lastSentAt.set(type, now)
    }

    // Snapshot everything synchronously: the caller may end the episode or
    // navigate right after this returns.
    const base = {
      type,
      url: sanitizedHref(),
      job_id: jobIdFromLocation(window.location.href),
      locale: document.documentElement.lang || 'en',
      release: process.env.NEXT_PUBLIC_COMMIT_SHA || '',
      detail: detail ?? null,
      tab_id: getTabId(),
      episode_id: currentEpisodeId,
    }
    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    const token = readAccessToken()
    if (token) headers.Authorization = `Bearer ${token}`
    const endpoint = `${apiBaseUrl()}/api/client-events`

    fingerprintWithTimeout()
      .then((device_fingerprint) =>
        fetch(endpoint, {
          method: 'POST',
          keepalive: true,
          headers,
          body: JSON.stringify({ ...base, device_fingerprint }),
        }),
      )
      .catch(() => {
        /* swallow — telemetry must never surface */
      })
  } catch {
    /* swallow */
  }
}
