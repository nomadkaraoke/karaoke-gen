import { act, renderHook } from '@testing-library/react'
import useManualSync from '../useManualSync'
import { LyricsSegment, Word } from '@/lib/lyrics-review/types'

/**
 * Regression tests for the Tap To Sync flow.
 *
 * Bug: when re-syncing a segment whose words already had timings, advancing to the
 * next word compared the just-tapped word's end against the *next* word's STALE start
 * (it hadn't been re-tapped yet) and pulled the end back to it, producing
 * end_time < start_time (negative duration). The segment sanitizer then reported
 * "Fixed N word timing(s) that fell outside this segment." on reopen.
 */

function w(id: string, text: string, start: number | null, end: number | null): Word {
  return { id, text, start_time: start, end_time: end }
}

function makeSegment(words: Word[]): LyricsSegment {
  const starts = words.map((x) => x.start_time).filter((t): t is number => t !== null)
  const ends = words.map((x) => x.end_time).filter((t): t is number => t !== null)
  return {
    id: 'seg-16',
    text: words.map((x) => x.text).join(' '),
    words,
    start_time: starts.length ? Math.min(...starts) : null,
    end_time: ends.length ? Math.max(...ends) : null,
  }
}

// Mirror EditModal.updateSegment: recompute segment bounds from word min/max.
function recompute(segment: LyricsSegment, newWords: Word[]): LyricsSegment {
  const starts = newWords.map((x) => x.start_time).filter((t): t is number => t !== null)
  const ends = newWords.map((x) => x.end_time).filter((t): t is number => t !== null)
  return {
    ...segment,
    words: newWords,
    text: newWords.map((x) => x.text).join(' '),
    start_time: starts.length ? Math.min(...starts) : null,
    end_time: ends.length ? Math.max(...ends) : null,
  }
}

// Re-fetch the handler between keydown and keyup: React commits `isSpacebarPressed` and
// re-creates the callback after keydown, and production re-registers the fresh closure via
// setModalHandler on every render. `result` is the renderHook result so `.current` is live.
function tap(result: { current: { handleSpacebar: (e: KeyboardEvent) => void } }) {
  const mk = (type: 'keydown' | 'keyup') =>
    ({ type, code: 'Space', preventDefault: () => {}, stopPropagation: () => {} } as unknown as KeyboardEvent)
  act(() => result.current.handleSpacebar(mk('keydown')))
  act(() => result.current.handleSpacebar(mk('keyup')))
}

describe('useManualSync — tap-to-sync end times', () => {
  beforeEach(() => {
    window.isAudioPlaying = false
    window.toggleAudioPlayback = jest.fn()
  })

  it('never produces a word whose end_time is before its start_time when re-syncing', () => {
    // Segment already fully synced with OLD timings — re-syncing moves words later.
    const initial = makeSegment([
      w('a', 'You', 88.9, 89.0),
      w('b', 'know', 89.0, 89.3),
      w('c', 'why,', 89.3, 89.8),
      w('d', 'the', 90.9, 91.0),
    ])

    let segment = initial
    let currentTime = 88.9

    const { result, rerender } = renderHook(
      ({ seg, time }) =>
        useManualSync({
          editedSegment: seg,
          currentTime: time,
          onPlaySegment: () => {},
          // Mirror production: recompute bounds and feed the new segment back in.
          updateSegment: (newWords: Word[]) => {
            segment = recompute(segment, newWords)
          },
        }),
      { initialProps: { seg: segment, time: currentTime } }
    )

    act(() => result.current.startManualSync())

    // Re-tap each word at a LATER playhead position than its old start.
    const tapTimes = [91.2, 91.6, 92.0, 92.4]
    tapTimes.forEach((t) => {
      currentTime = t
      rerender({ seg: segment, time: currentTime })
      tap(result)
      rerender({ seg: segment, time: currentTime })
    })

    // Invariant: every word with both timings must have end_time >= start_time.
    segment.words.forEach((word) => {
      if (word.start_time !== null && word.end_time !== null) {
        expect(word.end_time).toBeGreaterThanOrEqual(word.start_time)
      }
    })
  })

  it('extends the segment end when the last word is tapped near the old end', () => {
    // Segment ends at 3.00; re-tap so the LAST word lands at 2.90 — its default 0.5s tap
    // duration pushes its end to ~3.40, past the old end. The segment must grow to include it,
    // not hard-cut the word at 3.00.
    const initial = makeSegment([
      w('a', 'one', 0.5, 0.9),
      w('b', 'two', 1.0, 1.4),
      w('c', 'three', 2.9, 3.0),
    ])

    let segment = initial
    let currentTime = 0.5

    const { result, rerender } = renderHook(
      ({ seg, time }) =>
        useManualSync({
          editedSegment: seg,
          currentTime: time,
          onPlaySegment: () => {},
          updateSegment: (newWords: Word[]) => {
            segment = recompute(segment, newWords)
          },
        }),
      { initialProps: { seg: segment, time: currentTime } }
    )

    act(() => result.current.startManualSync())

    const tapTimes = [0.5, 1.0, 2.9]
    tapTimes.forEach((t) => {
      currentTime = t
      rerender({ seg: segment, time: currentTime })
      tap(result)
      rerender({ seg: segment, time: currentTime })
    })

    const lastWord = segment.words[segment.words.length - 1]
    // Last word's end extended past the old segment end (3.00)…
    expect(lastWord.end_time).toBeGreaterThan(3.0)
    // …and the segment end grew to include it (no hard cut at 3.00).
    expect(segment.end_time).toBeGreaterThanOrEqual(lastWord.end_time as number)
  })
})
