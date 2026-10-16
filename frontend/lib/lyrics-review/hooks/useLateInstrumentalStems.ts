'use client'

import { useEffect, useRef, useState } from 'react'
import type { InstrumentalState } from '@/lib/api'

/**
 * - `idle`: nothing to wait for (stems were in the page-load data, or polling is disabled).
 * - `waiting`: audio separation is still running — polling for the stems + analysis.
 * - `ready`: separation finished; `state` holds the fresh stems / analysis / verdict.
 * - `gave_up`: separation failed, or didn't finish within the polling window.
 */
export type LateInstrumentalStatus = 'idle' | 'waiting' | 'ready' | 'gave_up'

// Separation usually lands 2-3 minutes after review opens; mirror the vocals
// waveform loader's cadence (VocalsAudioDataLoader) and cap at 10 minutes.
export const LATE_STEMS_POLL_MS = 15_000
export const LATE_STEMS_MAX_POLLS = 40

const TERMINAL_JOB_STATUSES = new Set(['failed', 'cancelled'])

/**
 * Review opens as soon as LYRICS finish, but audio separation typically finishes
 * 2-3 minutes later — so the correction-data fetched at page load has no
 * instrumental stems, and the finish modal can't offer the instrumental chooser or
 * the backing-vocals waveform. When the page loaded without stems, poll the
 * instrumental-state endpoint until separation completes, then hand back the fresh
 * stems, backing analysis and auto-approval verdict for the page to overlay on its data.
 *
 * Waits for `audio_complete` (set only after stems, review OGGs and the backing
 * analysis are all written) rather than the first stem appearing, so the chooser
 * never shows with half its data (e.g. no ✓ recommendation).
 */
export function useLateInstrumentalStems({
  enabled,
  getInstrumentalState,
}: {
  /** Poll at all — true only when the page-load data had no instrumental stems. */
  enabled: boolean
  getInstrumentalState?: () => Promise<InstrumentalState>
}): { status: LateInstrumentalStatus; state: InstrumentalState | null } {
  const [status, setStatus] = useState<LateInstrumentalStatus>('idle')
  const [state, setState] = useState<InstrumentalState | null>(null)
  // The review page re-creates its API client on every render, so hold the getter
  // in a ref — keying the effect on it would restart polling on each re-render.
  const getterRef = useRef(getInstrumentalState)
  getterRef.current = getInstrumentalState
  const canPoll = enabled && !!getInstrumentalState

  useEffect(() => {
    if (!canPoll) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    setStatus('waiting')

    const poll = async (attempt: number) => {
      try {
        const getter = getterRef.current
        if (!getter) return
        const result = await getter()
        if (cancelled) return
        // An older backend (frontend deploys first; Cloud Run can lag tens of
        // minutes) omits `audio_complete` — fall back to "both stems are there".
        const stemIds = new Set(result.instrumental_options?.map((o) => o.id) ?? [])
        const complete =
          result.audio_complete ??
          (stemIds.has('clean') && stemIds.has('with_backing'))
        if (complete) {
          setState(result)
          setStatus('ready')
          return
        }
        if (result.job_status && TERMINAL_JOB_STATUSES.has(result.job_status)) {
          setStatus('gave_up')
          return
        }
      } catch (error) {
        // Transient (backend busy / network blip) — keep polling within the cap.
        if (cancelled) return
        console.warn('Instrumental stems poll failed, will retry', error)
      }
      if (attempt + 1 >= LATE_STEMS_MAX_POLLS) {
        setStatus('gave_up')
        return
      }
      timer = setTimeout(() => poll(attempt + 1), LATE_STEMS_POLL_MS)
    }

    poll(0)

    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [canPoll])

  return { status, state }
}
