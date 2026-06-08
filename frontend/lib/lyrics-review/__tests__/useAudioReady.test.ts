/**
 * @jest-environment jsdom
 */
import { renderHook, act } from '@testing-library/react'
import { useAudioReady } from '@/lib/lyrics-review/hooks/useAudioReady'
import { AUDIO_READY_EVENT } from '@/components/lyrics-review/AudioPlayer'

describe('useAudioReady', () => {
  afterEach(() => {
    delete (window as Window).isAudioReady
    delete (window as Window).audioLoadProgress
  })

  it('initialises from window state set before subscribing', () => {
    window.isAudioReady = true
    window.audioLoadProgress = 1
    const { result } = renderHook(() => useAudioReady())
    expect(result.current).toEqual({ ready: true, progress: 1 })
  })

  it('defaults to not-ready when nothing has been published', () => {
    const { result } = renderHook(() => useAudioReady())
    expect(result.current.ready).toBe(false)
    expect(result.current.progress).toBe(0)
  })

  it('updates when AUDIO_READY_EVENT is dispatched', () => {
    const { result } = renderHook(() => useAudioReady())

    act(() => {
      window.dispatchEvent(
        new CustomEvent(AUDIO_READY_EVENT, { detail: { ready: false, progress: 0.42 } })
      )
    })
    expect(result.current).toEqual({ ready: false, progress: 0.42 })

    act(() => {
      window.dispatchEvent(
        new CustomEvent(AUDIO_READY_EVENT, { detail: { ready: true, progress: 1 } })
      )
    })
    expect(result.current).toEqual({ ready: true, progress: 1 })
  })

  it('stops updating after unmount', () => {
    const { result, unmount } = renderHook(() => useAudioReady())
    unmount()
    act(() => {
      window.dispatchEvent(
        new CustomEvent(AUDIO_READY_EVENT, { detail: { ready: true, progress: 1 } })
      )
    })
    // Last observed value should remain the initial not-ready state.
    expect(result.current.ready).toBe(false)
  })
})
