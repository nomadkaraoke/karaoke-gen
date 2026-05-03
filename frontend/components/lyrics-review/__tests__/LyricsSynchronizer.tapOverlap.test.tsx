import { act, fireEvent, render, screen } from '@testing-library/react'
import LyricsSynchronizer from '../synchronizer/LyricsSynchronizer'
import type { LyricsSegment } from '@/lib/lyrics-review/types'

// Stub heavy children — we only care about timing logic, not rendering.
jest.mock('../synchronizer/TimelineCanvas', () => () => null)
jest.mock('../synchronizer/UpcomingWordsBar', () => () => null)

function makeSegment(words: Array<{ text: string; start: number | null; end: number | null }>): LyricsSegment {
  return {
    id: 'seg-1',
    text: words.map((w) => w.text).join(' '),
    start_time: words[0]?.start ?? null,
    end_time: words[words.length - 1]?.end ?? null,
    words: words.map((w, i) => ({
      id: `w-${i}`,
      text: w.text,
      start_time: w.start,
      end_time: w.end,
    })),
  }
}

function flushTimers() {
  jest.advanceTimersByTime(100)
}

describe('LyricsSynchronizer — tap-mode prevents overlapping word timings', () => {
  let capturedHandler: ((e: KeyboardEvent) => void) | undefined

  beforeEach(() => {
    capturedHandler = undefined
    // The component reads window.getAudioDuration / isAudioPlaying
    ;(window as unknown as { getAudioDuration: () => number }).getAudioDuration = () => 60
    ;(window as unknown as { isAudioPlaying: boolean }).isAudioPlaying = false
    ;(window as unknown as { toggleAudioPlayback: () => void }).toggleAudioPlayback = jest.fn()
    jest.useFakeTimers()
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  function setModalSpacebarHandler(handler: ((e: KeyboardEvent) => void) | undefined) {
    capturedHandler = handler
  }

  // Regression: previously, a tap on word A set A.end_time = A.start + 0.5s. Then
  // when word B was tapped within 500ms, handleKeyDown's "fix prev word" branch
  // was gated on prevWord.end_time === null and didn't fire, leaving A.end > B.start.
  // For songs with words sung in rapid succession this caused the karaoke video
  // to highlight 2-3 words simultaneously throughout. The fix must trim A.end
  // whenever B.start would precede it, regardless of whether end_time was
  // already set by the tap optimistic-end heuristic.
  it('trims previous word end_time when next tap arrives within 500ms', async () => {
    const onSave = jest.fn()
    const segments = [
      makeSegment([
        { text: 'We', start: null, end: null },
        { text: 'walk', start: null, end: null },
        { text: 'in', start: null, end: null },
      ]),
    ]

    const { rerender } = render(
      <LyricsSynchronizer
        segments={segments}
        currentTime={0}
        onPlaySegment={jest.fn()}
        onSave={onSave}
        onCancel={jest.fn()}
        setModalSpacebarHandler={setModalSpacebarHandler}
      />
    )

    // Enter manual-sync mode.
    fireEvent.click(screen.getByRole('button', { name: /start sync/i }))

    expect(capturedHandler).toBeDefined()
    const dispatch = (type: 'keydown' | 'keyup', t: number) => {
      // currentTime drives wordStartTimeRef. Re-render so the component's
      // currentTimeRef is updated before the event fires.
      rerender(
        <LyricsSynchronizer
          segments={segments}
          currentTime={t}
          onPlaySegment={jest.fn()}
          onSave={onSave}
          onCancel={jest.fn()}
          setModalSpacebarHandler={setModalSpacebarHandler}
        />
      )
      act(() => {
        const ev = new KeyboardEvent(type, { code: 'Space', bubbles: true })
        capturedHandler!(ev)
      })
    }

    // Word 0 ("We") — tap at t=2.0, release 50ms later (isTap=true → end = 2.5)
    dispatch('keydown', 2.0)
    // press duration tracked via Date.now(); fast-forward 50ms
    act(() => { jest.advanceTimersByTime(50) })
    dispatch('keyup', 2.05)

    // Word 1 ("walk") — tap at t=2.2 (200ms after word 0's start). Without the
    // fix, word 0 still claims to end at 2.5, overlapping word 1 by 300ms.
    dispatch('keydown', 2.2)
    act(() => { jest.advanceTimersByTime(50) })
    dispatch('keyup', 2.25)

    // Word 2 ("in") — tap at t=2.4
    dispatch('keydown', 2.4)
    act(() => { jest.advanceTimersByTime(50) })
    dispatch('keyup', 2.45)

    flushTimers()

    // Apply
    fireEvent.click(screen.getByRole('button', { name: /^apply$/i }))

    expect(onSave).toHaveBeenCalledTimes(1)
    const saved: LyricsSegment[] = onSave.mock.calls[0][0]
    const words = saved.flatMap((s) => s.words)

    expect(words).toHaveLength(3)
    expect(words[0].start_time).toBeCloseTo(2.0, 3)
    expect(words[1].start_time).toBeCloseTo(2.2, 3)
    expect(words[2].start_time).toBeCloseTo(2.4, 3)

    // Core assertion: word 0's end must NOT extend past word 1's start.
    expect(words[0].end_time!).toBeLessThanOrEqual(words[1].start_time!)
    expect(words[1].end_time!).toBeLessThanOrEqual(words[2].start_time!)
  })
})
