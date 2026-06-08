'use client'

import { useEffect, useState } from 'react'
import { AUDIO_READY_EVENT } from '@/components/lyrics-review/AudioPlayer'

export interface AudioReadyState {
  /** True once the lyrics-review audio can play through without rebuffering. */
  ready: boolean
  /** Buffered fraction of the audio file, 0..1. */
  progress: number
}

/**
 * Reactive subscription to the lyrics-review audio readiness.
 *
 * AudioPlayer is the single source of truth: it owns the <audio> element, sets
 * `window.isAudioReady` / `window.audioLoadProgress`, and dispatches
 * AUDIO_READY_EVENT on every change. Components that trigger playback (segment
 * play buttons, the edit modal) use this hook to disable their controls while
 * the media is still loading.
 */
export function useAudioReady(): AudioReadyState {
  const [state, setState] = useState<AudioReadyState>({ ready: false, progress: 0 })

  useEffect(() => {
    if (typeof window === 'undefined') return

    // Sync immediately in case readiness was published before we subscribed.
    setState({
      ready: !!window.isAudioReady,
      progress: window.audioLoadProgress ?? 0,
    })

    const onChange = (e: Event) => {
      const detail = (e as CustomEvent<AudioReadyState>).detail
      if (detail) setState({ ready: !!detail.ready, progress: detail.progress ?? 0 })
    }

    window.addEventListener(AUDIO_READY_EVENT, onChange)
    return () => window.removeEventListener(AUDIO_READY_EVENT, onChange)
  }, [])

  return state
}
