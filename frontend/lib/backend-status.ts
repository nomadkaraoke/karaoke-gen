'use client'

/**
 * Global backend-connectivity status.
 *
 * The backend runs on Cloud Run with `--min-instances 1`. When Cloud Run recycles
 * that single instance (routine host maintenance / instance max-lifetime), there is
 * a brief window where the origin is unreachable and requests HANG (Cloud Run holds
 * them during the cold start) even though nothing is actually broken — and any
 * karaoke jobs already rendering keep running untouched.
 *
 * We want ONE reassuring app-wide banner during those blips, but only when there's
 * real evidence of a problem — NOT merely because a request was a little slow. So
 * status is derived purely from **how long the oldest in-flight backend GET has been
 * outstanding**: a read that completes (even a slowish 3–5s one) shows nothing; only
 * a read stalled past STALL_RECONNECTING_MS surfaces the hint, escalating past
 * STALL_UNAVAILABLE_MS to the full message. This matches "only tell the user once
 * something has genuinely been trying to load for >10s / timed out."
 *
 * Only GETs are tracked: reads are what a page waits on during a recycle, and long
 * POSTs (search, generate, auto-correct) are legitimately slow and must never trip
 * the banner. See lib/api.ts (apiFetch) for the begin/endRequest wiring.
 */

import { useSyncExternalStore } from 'react'

export type BackendStatus =
  /** No stalled reads — everything is completing in a reasonable time. */
  | 'online'
  /** A read has been outstanding a while; show a subtle "reconnecting" hint. */
  | 'reconnecting'
  /** A read has been stalled long enough to show the full "temporarily unavailable". */
  | 'unavailable'

/** A tracked read must be outstanding at least this long before we show ANYTHING —
 *  so a normal slow-but-successful load never surfaces the banner. */
export const STALL_RECONNECTING_MS = 10_000
/** ...and this long before we escalate to the full assertive message. */
export const STALL_UNAVAILABLE_MS = 20_000

let nextId = 1
/** id -> startedAt (ms) for every tracked backend GET currently in flight. */
const inFlight = new Map<number, number>()
/** Dev/preview override: when non-null, forces the reported status. */
let devOverride: BackendStatus | null = null

let status: BackendStatus = 'online'
let ticker: ReturnType<typeof setInterval> | null = null

const listeners = new Set<() => void>()

function emit() {
  listeners.forEach((l) => l())
}

function setStatus(next: BackendStatus) {
  if (status === next) return
  status = next
  emit()
}

function computeFromStalls(): BackendStatus {
  if (inFlight.size === 0) return 'online'
  let oldest = Infinity
  for (const startedAt of inFlight.values()) {
    if (startedAt < oldest) oldest = startedAt
  }
  const age = Date.now() - oldest
  if (age >= STALL_UNAVAILABLE_MS) return 'unavailable'
  if (age >= STALL_RECONNECTING_MS) return 'reconnecting'
  return 'online'
}

function recompute() {
  setStatus(devOverride ?? computeFromStalls())
  // Stop the clock once nothing is outstanding (and no dev override needs it).
  if (inFlight.size === 0 && devOverride === null && ticker) {
    clearInterval(ticker)
    ticker = null
  }
}

function ensureTicker() {
  if (ticker) return
  // Re-evaluate every second so a request that keeps hanging escalates over time.
  ticker = setInterval(recompute, 1000)
}

/**
 * Mark a tracked backend read as started. Returns an id to pass to `endRequest` when
 * it settles (success OR failure — either way it's no longer outstanding).
 */
export function beginRequest(): number {
  const id = nextId++
  inFlight.set(id, Date.now())
  ensureTicker()
  recompute()
  return id
}

/** Mark a tracked read as settled. */
export function endRequest(id: number): void {
  if (inFlight.delete(id)) recompute()
}

export function getBackendStatus(): BackendStatus {
  return status
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/**
 * React hook: subscribe to the current backend connectivity status. Server render
 * always yields "online" so the banner never appears in the static HTML.
 */
export function useBackendStatus(): BackendStatus {
  return useSyncExternalStore(subscribe, getBackendStatus, () => 'online')
}

/**
 * Dev/preview-only escape hatch so the outage UX can be demoed without waiting on a
 * real stall. Exposed on `window.__nkBackendStatus` and no-ops on the production
 * consumer host. Not referenced by app code paths.
 */
export function __installBackendStatusDevHook() {
  if (typeof window === 'undefined') return
  if (window.location.hostname === 'gen.nomadkaraoke.com') return
  const simulate = (s: BackendStatus | null) => {
    devOverride = s
    if (s !== null) ensureTicker()
    recompute()
  }
  ;(window as unknown as { __nkBackendStatus?: unknown }).__nkBackendStatus = {
    reconnecting: () => simulate('reconnecting'),
    unavailable: () => simulate('unavailable'),
    online: () => simulate(null),
    get: () => getBackendStatus(),
  }
}
