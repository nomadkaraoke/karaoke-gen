import { useCallback, useEffect, useRef } from 'react'
import { warmupEncodingWorker, heartbeatEncodingWorker } from '@/lib/api'

/**
 * Keeps the encoding worker VM warm while a reviewer is on the lyrics-review page,
 * so the preview video and the final render don't pay a VM cold start.
 *
 * The backend idle-shutdown stops the serving VM once `last_activity_at` is older
 * than IDLE_TIMEOUT_MINUTES (5 min since 2026-09-26, checked every 2 min — see
 * infrastructure/config.py EncodingWorkerConfig). Previously the only heartbeat
 * fired on lyric *edits*, so a reviewer reading or listening without editing would
 * let the VM stop. Now:
 *   - warmup once on mount (starts the VM if stopped),
 *   - heartbeat on edits (debounced to one per HEARTBEAT_MIN_GAP_MS),
 *   - heartbeat every REVIEW_HEARTBEAT_INTERVAL_MS while the tab is visible AND
 *     the reviewer interacted within REVIEW_ACTIVE_WINDOW_MS (an abandoned tab
 *     stops keeping the VM alive),
 *   - warmup again when the tab becomes visible after being hidden (the VM may
 *     have idled out meanwhile).
 */

// Must stay comfortably below the backend idle timeout (5 min) — asserted by
// infrastructure/test_encoding_worker_config.py::test_idle_shutdown_is_fast_but_outlives_review_heartbeat.
export const REVIEW_HEARTBEAT_INTERVAL_MS = 2 * 60_000
export const HEARTBEAT_MIN_GAP_MS = 60_000
// Keep-alive stops this long after the last interaction (≈ the old 15-min idle window).
export const REVIEW_ACTIVE_WINDOW_MS = 15 * 60_000

const INTERACTION_EVENTS = ['pointerdown', 'keydown', 'wheel', 'touchstart'] as const

export function useEncodingWorkerKeepAlive(jobId: string | null | undefined, enabled: boolean) {
  const lastHeartbeat = useRef(0)
  const lastInteraction = useRef(Date.now())
  const active = enabled && !!jobId

  const sendHeartbeat = useCallback(() => {
    if (!active || !jobId) return
    lastInteraction.current = Date.now()
    const now = Date.now()
    if (now - lastHeartbeat.current > HEARTBEAT_MIN_GAP_MS) {
      lastHeartbeat.current = now
      heartbeatEncodingWorker(jobId)
    }
  }, [active, jobId])

  // Warm up once when the review page loads (cloud mode only).
  useEffect(() => {
    if (active && jobId) warmupEncodingWorker(jobId)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!active || !jobId) return

    const markInteraction = () => {
      lastInteraction.current = Date.now()
    }
    INTERACTION_EVENTS.forEach((evt) => window.addEventListener(evt, markInteraction, { passive: true }))

    const tick = () => {
      if (document.visibilityState !== 'visible') return
      if (Date.now() - lastInteraction.current > REVIEW_ACTIVE_WINDOW_MS) return
      lastHeartbeat.current = Date.now()
      heartbeatEncodingWorker(jobId)
    }
    const intervalId = setInterval(tick, REVIEW_HEARTBEAT_INTERVAL_MS)

    const onVisibilityChange = () => {
      if (document.visibilityState !== 'visible') return
      lastInteraction.current = Date.now()
      lastHeartbeat.current = Date.now()
      // Starts the VM if it idled out while the tab was hidden; otherwise just
      // refreshes last_activity_at.
      warmupEncodingWorker(jobId)
    }
    document.addEventListener('visibilitychange', onVisibilityChange)

    return () => {
      INTERACTION_EVENTS.forEach((evt) => window.removeEventListener(evt, markInteraction))
      clearInterval(intervalId)
      document.removeEventListener('visibilitychange', onVisibilityChange)
    }
  }, [active, jobId])

  return { sendHeartbeat }
}
