'use client'

/**
 * Version-awareness for cache-busting.
 *
 * - isStale()    — compares the compiled NEXT_PUBLIC_COMMIT_SHA against /version.json.
 * - hardReload() — unregisters SWs, clears caches, navigates to url?_v=<sha>.
 *                  Includes a 60s circuit breaker to prevent reload loops.
 * - handleChunkLoadError() — pattern-matches common chunk errors and reloads.
 */

const STORAGE_KEY = 'karaoke_last_hard_reload'
const CIRCUIT_BREAKER_MS = 60_000

let _buildShaOverride: string | null = null

export function __resetForTest() {
  _buildShaOverride = null
  if (typeof sessionStorage !== 'undefined') {
    sessionStorage.removeItem(STORAGE_KEY)
  }
}

export function __setBuildShaForTest(sha: string) {
  _buildShaOverride = sha
}

function currentBuildSha(): string {
  if (_buildShaOverride !== null) return _buildShaOverride
  return process.env.NEXT_PUBLIC_COMMIT_SHA || 'dev'
}

export interface StaleResult {
  stale: boolean
  latestSha: string
  currentSha: string
}

export async function isStale(): Promise<StaleResult> {
  const current = currentBuildSha()
  // 'dev' means local — never surface a stale warning in dev
  if (current === 'dev' || current === '') {
    return { stale: false, latestSha: current, currentSha: current }
  }
  try {
    const res = await fetch('/version.json', { cache: 'no-store' })
    if (!res.ok) throw new Error(`status ${res.status}`)
    const data = (await res.json()) as { build_sha?: string }
    const latest = data?.build_sha ?? current
    return { stale: latest !== current, latestSha: latest, currentSha: current }
  } catch {
    // Fail-safe: if we can't check, don't claim stale (would spam users).
    return { stale: false, latestSha: current, currentSha: current }
  }
}

function withinCircuitBreaker(): boolean {
  if (typeof sessionStorage === 'undefined') return false
  const last = sessionStorage.getItem(STORAGE_KEY)
  if (!last) return false
  const ts = Number(last)
  if (!Number.isFinite(ts)) return false
  return Date.now() - ts < CIRCUIT_BREAKER_MS
}

function markReload() {
  try {
    sessionStorage.setItem(STORAGE_KEY, String(Date.now()))
  } catch {
    /* private mode — ok */
  }
}

export function hardReload(latestSha?: string): boolean {
  if (typeof window === 'undefined') return false
  if (withinCircuitBreaker()) {
    if (typeof console !== 'undefined') {
      console.warn('[version-check] hardReload suppressed by circuit breaker')
    }
    return false
  }
  markReload()

  // Best-effort cache-busting before reload. These are fire-and-forget —
  // if they're slow, the navigation still proceeds.
  try {
    if ('serviceWorker' in navigator) {
      void navigator.serviceWorker
        .getRegistrations()
        .then((regs) => regs.forEach((r) => r.unregister()))
        .catch(() => {})
    }
  } catch {
    /* ignore */
  }
  try {
    if (typeof caches !== 'undefined') {
      void caches
        .keys()
        .then((keys) => keys.forEach((k) => caches.delete(k)))
        .catch(() => {})
    }
  } catch {
    /* ignore */
  }

  const sep = window.location.pathname.includes('?') ? '&' : '?'
  const buster = latestSha && latestSha !== 'dev' ? latestSha : String(Date.now())
  window.location.assign(`${window.location.pathname}${sep}_v=${encodeURIComponent(buster)}`)
  return true
}

/**
 * Returns true if `err` looks like a chunk-load error from a post-deploy
 * mismatch. Next.js surfaces these as `ChunkLoadError` or messages mentioning
 * "Loading chunk" / "dynamically imported module".
 */
export function isChunkLoadError(err: unknown): boolean {
  if (!err) return false
  const e = err as { name?: string; message?: string }
  if (e.name === 'ChunkLoadError') return true
  const msg = typeof e.message === 'string' ? e.message : ''
  return (
    /ChunkLoadError/i.test(msg) ||
    /Loading chunk [\w-]+ failed/i.test(msg) ||
    /Failed to fetch dynamically imported module/i.test(msg) ||
    /Importing a module script failed/i.test(msg)
  )
}

/**
 * Ambient poll every 10 minutes. Only installs one poller per tab.
 */
let pollHandle: ReturnType<typeof setInterval> | null = null

export function startAmbientVersionPoll(
  onStale: (r: StaleResult) => void,
  intervalMs = 10 * 60 * 1000,
) {
  if (typeof window === 'undefined') return () => {}
  if (pollHandle !== null) return () => {}
  const tick = async () => {
    const r = await isStale()
    if (r.stale) onStale(r)
  }
  pollHandle = setInterval(() => void tick(), intervalMs)
  return () => {
    if (pollHandle !== null) {
      clearInterval(pollHandle)
      pollHandle = null
    }
  }
}
