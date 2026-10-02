'use client'

/**
 * Global backend-connectivity status.
 *
 * The backend runs on Cloud Run (now min-instances 0, see COLD STARTS below). When it recycles
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
 *
 * A stall alone is still not proof of an outage: some reads legitimately run past
 * the thresholds (e.g. first-time instrumental-analysis / lyrics-review loads that
 * transcode audio server-side). So once a stall crosses the threshold, we confirm
 * with a cheap /api/health probe (registered via configureHealthProbe): if the probe
 * answers, the backend is reachable and the endpoint is just slow — no banner. Only
 * a probe that fails (or times out, as it does during a recycle when the origin
 * hangs) lets the banner surface.
 */

/*
 * COLD STARTS (min-instances 0). The backend now scales to zero when idle, so the
 * first request after a quiet spell waits ~15-20s while Cloud Run starts an
 * instance (requests are QUEUED, so reads hang rather than fail, and the health
 * probe times out exactly like it does during a recycle). From the client a cold
 * start and a recycle look identical, so we tell them apart by recency:
 *
 *   - If no backend response has succeeded in the REACHABLE_FRESH_MS before the
 *     oldest stalled read began (fresh page load, or a tab left idle long enough
 *     for the backend to scale to zero), a confirmed stall is a cold start: show
 *     the calm 'waking' state ("Starting up our karaoke servers...") from
 *     WAKING_SHOW_MS, without warning colours.
 *   - If that stall outlasts WAKING_ESCALATE_MS it's no longer a plausible cold
 *     start, so the normal reconnecting/unavailable logic takes over.
 *   - If the backend WAS answering just before the stall, it's a recycle/outage:
 *     the normal reconnecting/unavailable logic applies straight away.
 *
 * prewarmBackend() fires an untracked GET /api/health on app load (and when a tab
 * returns after a long absence) so the instance starts while the user is still
 * reading the page; its success also marks the backend reachable.
 */

import { useSyncExternalStore } from 'react'

export type BackendStatus =
  /** No stalled reads — everything is completing in a reasonable time. */
  | 'online'
  /** A read has been outstanding a while; show a subtle "reconnecting" hint. */
  | 'reconnecting'
  /** A read has been stalled long enough to show the full "temporarily unavailable". */
  | 'unavailable'
  /** Cold start: the backend hasn't answered recently and is (most likely) waking
   *  up from scale-to-zero. Calm "starting up our servers" message. */
  | 'waking'

/** A tracked read must be outstanding at least this long before we show ANYTHING —
 *  so a normal slow-but-successful load never surfaces the banner. */
export const STALL_RECONNECTING_MS = 10_000
/** ...and this long before we escalate to the full assertive message. */
export const STALL_UNAVAILABLE_MS = 20_000
/** A health-probe verdict is trusted this long before we re-probe during a stall. */
export const PROBE_FRESH_MS = 10_000
/** Escalating to the full "unavailable" card needs this many CONSECUTIVE failed
 *  probes — one failed 4s probe against a briefly-busy instance (e.g. a heavy
 *  transcode pinning the CPU) is not an outage. The reconnecting pill still
 *  shows after a single failure; the assertive message waits for confirmation. */
export const UNAVAILABLE_PROBE_FAILURES = 2
/** A backend response this recent (relative to when the oldest stalled read began)
 *  means the backend was up and has since stopped answering: a recycle/outage,
 *  not a cold start. Cloud Run scales an idle service to zero after ~15 min, so
 *  anything older than this may well be a cold start. */
export const REACHABLE_FRESH_MS = 10 * 60_000
/** Cold-start mode: show the calm waking state once a read has been outstanding
 *  this long AND the health probe has failed (it times out at ~4s on a cold
 *  instance, so in practice the waking state appears ~4-5s into a cold start). */
export const WAKING_SHOW_MS = 3_000
/** Cold-start mode probes once the oldest read has been outstanding this long
 *  (instead of waiting for STALL_RECONNECTING_MS). Short enough that the waking
 *  state appears ~5s into a cold start; long enough that the normal warm-backend
 *  page load (reads done in well under a second) never sends an extra probe. */
export const COLD_PROBE_AFTER_MS = 1_000
/** Cold starts take ~15-20s. A stall past this is no longer plausibly a cold
 *  start, so we escalate to the normal reconnecting/unavailable banner. */
export const WAKING_ESCALATE_MS = 60_000
/** Re-prewarm when a tab becomes visible after being hidden at least this long. */
export const PREWARM_AFTER_HIDDEN_MS = 10 * 60_000

let nextId = 1
/** id -> startedAt (ms) for every tracked backend GET currently in flight. */
const inFlight = new Map<number, number>()
/** Dev/preview override: when non-null, forces the reported status. */
let devOverride: BackendStatus | null = null

let status: BackendStatus = 'online'
let ticker: ReturnType<typeof setInterval> | null = null

/** Cheap reachability check (e.g. GET /api/health). Must resolve within a few
 *  seconds — implementations race against their own timeout and NEVER reject. */
type HealthProbe = () => Promise<boolean>
let healthProbe: HealthProbe | null = null
let probeInFlight = false
/** Last probe verdict (null = no verdict yet) and when it settled. */
let lastProbeOk: boolean | null = null
let lastProbeAt = 0
/** Failed probes in a row (reset by any success or reconfiguration). */
let consecutiveProbeFailures = 0
/** Bumped on configureHealthProbe so a probe from a previous config can't land. */
let probeEpoch = 0

/** When a backend response last succeeded (0 = never in this page session). */
let lastReachableAt = 0
/** Start of the current cold-start episode (null = none); cleared by any response. */
let coldEpisodeStartedAt: number | null = null

const listeners = new Set<() => void>()

function emit() {
  listeners.forEach((l) => l())
}

function setStatus(next: BackendStatus) {
  if (status === next) return
  status = next
  emit()
}

function oldestStartedAt(): number {
  let oldest = Infinity
  for (const startedAt of inFlight.values()) {
    if (startedAt < oldest) oldest = startedAt
  }
  return oldest
}

function computeFromStalls(): BackendStatus {
  if (inFlight.size === 0) return 'online'
  const age = Date.now() - oldestStartedAt()
  if (age >= STALL_UNAVAILABLE_MS) return 'unavailable'
  if (age >= STALL_RECONNECTING_MS) return 'reconnecting'
  return 'online'
}

/** True when the oldest outstanding read is (plausibly) waiting on a cold start:
 *  nothing from the backend succeeded in the REACHABLE_FRESH_MS before it began,
 *  and it hasn't yet outlasted a plausible cold start. */
function inColdStartWindow(): boolean {
  if (inFlight.size === 0) return false
  const oldest = oldestStartedAt()
  const notRecentlyReachable =
    lastReachableAt === 0 || lastReachableAt < oldest - REACHABLE_FRESH_MS
  if (!notRecentlyReachable) return false
  // Escalation is measured from when this cold episode BEGAN, not from the oldest
  // read still pending — otherwise, as stalled reads time out and newer (e.g.
  // polling) reads become the oldest, a genuine outage would flap
  // unavailable → waking → unavailable. Only a backend response ends the episode.
  if (coldEpisodeStartedAt === null) coldEpisodeStartedAt = oldest
  return Date.now() - coldEpisodeStartedAt < WAKING_ESCALATE_MS
}

/** Fire a health probe if one isn't running and the last verdict has gone stale. */
function maybeStartProbe() {
  if (!healthProbe || probeInFlight) return
  if (lastProbeOk !== null && Date.now() - lastProbeAt < PROBE_FRESH_MS) return
  probeInFlight = true
  const epoch = probeEpoch
  const record = (ok: boolean) => {
    if (epoch !== probeEpoch) return
    lastProbeOk = ok
    consecutiveProbeFailures = ok ? 0 : consecutiveProbeFailures + 1
    if (ok) lastReachableAt = Date.now()
  }
  healthProbe()
    .then(
      (ok) => record(ok),
      () => record(false),
    )
    .finally(() => {
      if (epoch !== probeEpoch) return
      probeInFlight = false
      lastProbeAt = Date.now()
      recompute()
    })
}

function recompute() {
  if (devOverride === null && inColdStartWindow()) {
    // Cold start: probe after just COLD_PROBE_AFTER_MS (rather than waiting for
    // the 10s stall threshold) so the calm waking state can appear within a few seconds. The
    // probe still gates it: a warm backend answers /api/health instantly, so a
    // merely slow endpoint never shows the waking state.
    const age = Date.now() - oldestStartedAt()
    if (healthProbe && age >= COLD_PROBE_AFTER_MS) maybeStartProbe()
    const probeSaysDown = !healthProbe || lastProbeOk === false
    setStatus(age >= WAKING_SHOW_MS && probeSaysDown ? 'waking' : 'online')
    return
  }
  let next = devOverride ?? computeFromStalls()
  // A stall only becomes a banner once the health probe CONFIRMS the backend is
  // unreachable. While the probe answers OK (or hasn't answered yet), the stalled
  // read is treated as a legitimately slow endpoint and the banner stays hidden.
  if (devOverride === null && next !== 'online' && healthProbe) {
    maybeStartProbe()
    if (lastProbeOk !== false) {
      next = 'online'
    } else if (next === 'unavailable' && consecutiveProbeFailures < UNAVAILABLE_PROBE_FAILURES) {
      // One failed probe can just be a briefly-pegged instance; keep the calm
      // pill until a second consecutive failure confirms real unreachability.
      next = 'reconnecting'
    }
  }
  setStatus(next)
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

/**
 * Record that the backend just answered (any non-transient HTTP response, from any
 * request). This is what distinguishes "was up, now stalled" (recycle/outage:
 * normal banner) from "hasn't answered lately" (cold start: calm waking state).
 */
export function markBackendReachable(): void {
  lastReachableAt = Date.now()
  coldEpisodeStartedAt = null
  // The backend just answered, so any earlier failed-probe verdict is stale: drop
  // it rather than let it vouch for a future stall (re-probe instead).
  if (lastProbeOk === false) {
    lastProbeOk = null
    consecutiveProbeFailures = 0
  }
  if (inFlight.size > 0 || status !== 'online') recompute()
}

/** Untracked warm-up request (GET /api/health). Must never reject. */
type Prewarm = () => Promise<boolean>
let prewarmFn: Prewarm | null = null
let prewarmInFlight = false
let hiddenAt: number | null = null
let visibilityListenerInstalled = false

/** Registered once by lib/api.ts (keeps this module free of fetch/URL concerns). */
export function configurePrewarm(fn: Prewarm | null): void {
  prewarmFn = fn
  prewarmInFlight = false
}

/**
 * Fire-and-forget wake-up for a backend that may have scaled to zero, so the cold
 * start overlaps with the user reading the page instead of blocking their first
 * action. Untracked: it never drives the banner itself. A no-op if one is already
 * in flight or the backend answered within REACHABLE_FRESH_MS.
 */
export function prewarmBackend(): void {
  if (!prewarmFn || prewarmInFlight) return
  if (lastReachableAt !== 0 && Date.now() - lastReachableAt < REACHABLE_FRESH_MS) return
  prewarmInFlight = true
  prewarmFn()
    .then(
      (ok) => {
        if (ok) markBackendReachable()
      },
      () => {},
    )
    .finally(() => {
      prewarmInFlight = false
    })
}

/**
 * Pre-warm now, and again whenever the tab becomes visible after being hidden for
 * PREWARM_AFTER_HIDDEN_MS (long enough that the backend may have scaled to zero).
 * Idempotent; call from any client component mounted app-wide, passing
 * `__backendPrewarm` from lib/api.ts.
 */
export function installBackendPrewarm(fn?: Prewarm): void {
  if (typeof document === 'undefined') return
  // Callers pass the fetch explicitly so the pre-warm works even on a page that
  // hasn't (yet) loaded lib/api.ts, which is where it's otherwise registered.
  if (fn && !prewarmFn) configurePrewarm(fn)
  prewarmBackend()
  if (visibilityListenerInstalled) return
  visibilityListenerInstalled = true
  if (document.visibilityState === 'hidden') hiddenAt = Date.now()
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') {
      hiddenAt = Date.now()
      return
    }
    const wasHiddenFor = hiddenAt === null ? 0 : Date.now() - hiddenAt
    hiddenAt = null
    if (wasHiddenFor >= PREWARM_AFTER_HIDDEN_MS) prewarmBackend()
  })
}

/**
 * Register the reachability probe used to confirm a stall before the banner shows.
 * The probe must never reject and must settle within a few seconds (race against
 * your own timeout). Registered once by lib/api.ts. Passing null (tests) restores
 * pure stall-based behavior and clears any cached verdict.
 */
export function configureHealthProbe(probe: HealthProbe | null): void {
  probeEpoch++
  healthProbe = probe
  probeInFlight = false
  lastProbeOk = null
  lastProbeAt = 0
  consecutiveProbeFailures = 0
}

/** Diagnostic snapshot for degradation telemetry (see lib/degradation-events). */
export function getBackendStatusDebug(): {
  oldestStallMs: number
  inFlightCount: number
  lastProbeOk: boolean | null
  consecutiveProbeFailures: number
  /** ms since the backend last answered (null = never this session). */
  sinceReachableMs: number | null
} {
  return {
    oldestStallMs: inFlight.size === 0 ? 0 : Date.now() - oldestStartedAt(),
    inFlightCount: inFlight.size,
    lastProbeOk,
    consecutiveProbeFailures,
    sinceReachableMs: lastReachableAt === 0 ? null : Date.now() - lastReachableAt,
  }
}

/** Test-only: reset all module state (reachability, prewarm, in-flight reads). */
export function __resetBackendStatusForTest(): void {
  inFlight.clear()
  devOverride = null
  lastReachableAt = 0
  coldEpisodeStartedAt = null
  prewarmInFlight = false
  hiddenAt = null
  configureHealthProbe(null)
  if (ticker) {
    clearInterval(ticker)
    ticker = null
  }
  status = 'online'
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
    waking: () => simulate('waking'),
    reconnecting: () => simulate('reconnecting'),
    unavailable: () => simulate('unavailable'),
    online: () => simulate(null),
    get: () => getBackendStatus(),
  }
}
