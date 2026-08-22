'use client'

/**
 * Global backend-connectivity status.
 *
 * The backend runs on Cloud Run with `--min-instances 1`. When Cloud Run recycles
 * that single instance (routine host maintenance / instance max-lifetime), there is
 * a brief (~1-2 min worst case, usually well under) window where the origin is
 * unreachable and API calls fail even though nothing is actually broken — and any
 * karaoke jobs already rendering (Cloud Run Jobs / GCE encoding VMs) keep running
 * untouched. See infrastructure/modules/monitoring.py for the server-side story.
 *
 * `apiFetch` (lib/api.ts) reports request outcomes here so a single app-wide banner
 * can reassure the user during those blips instead of every screen showing its own
 * scary generic error. This is a tiny framework-agnostic store (module singleton +
 * listeners) consumed via the `useBackendStatus` hook.
 */

import { useSyncExternalStore } from 'react'

export type BackendStatus =
  /** Requests are succeeding (or we have no evidence of trouble). */
  | 'online'
  /** A request just failed and we're transparently retrying — show a subtle hint. */
  | 'reconnecting'
  /** Trouble has persisted past the threshold — show the gentle full message. */
  | 'unavailable'

/**
 * How long connectivity trouble must persist before we escalate from the subtle
 * "reconnecting" hint to the full "temporarily unavailable" message. Kept in sync
 * with the read-retry budget in lib/api.ts so a request that exhausts its retries
 * lands right around the same time this escalates.
 */
export const UNAVAILABLE_AFTER_MS = 10_000

let status: BackendStatus = 'online'
let firstTroubleAt: number | null = null
let escalationTimer: ReturnType<typeof setTimeout> | null = null

const listeners = new Set<() => void>()

function emit() {
  listeners.forEach((l) => l())
}

function setStatus(next: BackendStatus) {
  if (status === next) return
  status = next
  emit()
}

function clearEscalationTimer() {
  if (escalationTimer) {
    clearTimeout(escalationTimer)
    escalationTimer = null
  }
}

/**
 * Report that a backend request attempt failed and we're about to retry (or it
 * ultimately failed but might just be a transient blip). Moves us to "reconnecting"
 * immediately and arms a timer that escalates to "unavailable" if trouble persists.
 */
export function reportBackendTrouble() {
  if (firstTroubleAt === null) {
    firstTroubleAt = Date.now()
  }
  if (status === 'online') {
    setStatus('reconnecting')
  }
  if (status !== 'unavailable' && escalationTimer === null) {
    const elapsed = Date.now() - (firstTroubleAt ?? Date.now())
    const remaining = Math.max(0, UNAVAILABLE_AFTER_MS - elapsed)
    escalationTimer = setTimeout(() => {
      escalationTimer = null
      // Still no success by now → this is more than a momentary blip.
      if (firstTroubleAt !== null) setStatus('unavailable')
    }, remaining)
  }
}

/**
 * Report that trouble is confirmed sustained (e.g. a read exhausted its full retry
 * budget). Escalates to "unavailable" right away without waiting on the timer.
 */
export function reportBackendUnavailable() {
  if (firstTroubleAt === null) firstTroubleAt = Date.now()
  clearEscalationTimer()
  setStatus('unavailable')
}

/**
 * Report a successful backend request. Clears any trouble state and returns us to
 * "online" — the banner disappears the moment connectivity is restored.
 */
export function reportBackendOnline() {
  firstTroubleAt = null
  clearEscalationTimer()
  setStatus('online')
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
 * Dev/preview-only escape hatch so the outage UX can be demoed without actually
 * taking the backend down. Exposed on `window.__nkBackendStatus` and no-ops on the
 * production consumer build. Not referenced by app code paths.
 */
export function __installBackendStatusDevHook() {
  if (typeof window === 'undefined') return
  const host = window.location.hostname
  const isProd = host === 'gen.nomadkaraoke.com'
  if (isProd) return
  ;(window as unknown as { __nkBackendStatus?: unknown }).__nkBackendStatus = {
    reconnecting: () => reportBackendTrouble(),
    unavailable: () => reportBackendUnavailable(),
    online: () => reportBackendOnline(),
    get: () => getBackendStatus(),
  }
}
