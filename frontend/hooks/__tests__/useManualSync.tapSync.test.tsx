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

  it('expands a tapped word to fill the gap up to the next word onset', () => {
    // "a simple call [gap] seems" — after tapping "call", the next word "seems" starts ~2s
    // later. "call" should stretch to ~seems.start (no dead gap), not stay a fixed 0.5s block.
    const initial = makeSegment([
      w('a', 'a', null, null),
      w('b', 'call', null, null),
      w('c', 'seems', null, null),
    ])

    let segment = initial
    let currentTime = 0

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

    // Tap "a" @1.0, "call" @2.0, then "seems" @2.8 — a 0.8s gap after "call" (within the 1s cap).
    const tapTimes = [1.0, 2.0, 2.8]
    tapTimes.forEach((t) => {
      currentTime = t
      rerender({ seg: segment, time: currentTime })
      tap(result)
      rerender({ seg: segment, time: currentTime })
    })

    const call = segment.words[1]
    // "call" fills the gap right up to "seems".start (2.8) minus a tiny overlap buffer,
    // instead of the old fixed 2.0 + 0.5 = 2.5 that left a dead gap.
    expect(call.end_time as number).toBeGreaterThan(2.7)
    expect(call.end_time as number).toBeLessThan(2.8)
    // No overlap with the next word.
    expect(call.end_time as number).toBeLessThanOrEqual(segment.words[2].start_time as number)
  })

  it('caps a tapped word at 1s when the gap to the next word is large', () => {
    // Gap of 6s after "call" — the fill must cap at 1s (word can't stretch the whole instrumental).
    const initial = makeSegment([
      w('a', 'call', null, null),
      w('b', 'seems', null, null),
    ])

    let segment = initial
    let currentTime = 0

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

    const tapTimes = [1.0, 7.0]
    tapTimes.forEach((t) => {
      currentTime = t
      rerender({ seg: segment, time: currentTime })
      tap(result)
      rerender({ seg: segment, time: currentTime })
    })

    const call = segment.words[0]
    // Filled beyond the 0.5s default, but capped at start + MAX_TAP_GAP_FILL_SECONDS (1.0 + 1.0),
    // well short of "seems".start (7.0).
    expect(call.end_time as number).toBeCloseTo(1.0 + 1.0, 5)
  })

  it('does not gap-fill a held word (respects the deliberate release)', () => {
    // A HOLD sets the word's end to the release playhead. A following gap must not stretch it.
    const initial = makeSegment([
      w('a', 'ohhh', null, null),
      w('b', 'yeah', null, null),
    ])

    let segment = initial
    let currentTime = 0

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

    const mk = (type: 'keydown' | 'keyup') =>
      ({ type, code: 'Space', preventDefault: () => {}, stopPropagation: () => {} } as unknown as KeyboardEvent)

    // HOLD "ohhh": press at playhead 1.0, release at playhead 2.0 after >200ms held.
    const nowSpy = jest.spyOn(Date, 'now')
    currentTime = 1.0
    rerender({ seg: segment, time: currentTime })
    nowSpy.mockReturnValue(10_000)
    act(() => result.current.handleSpacebar(mk('keydown')))
    currentTime = 2.0
    rerender({ seg: segment, time: currentTime })
    nowSpy.mockReturnValue(10_400) // held 400ms > TAP_THRESHOLD_MS
    act(() => result.current.handleSpacebar(mk('keyup')))
    nowSpy.mockRestore()
    rerender({ seg: segment, time: currentTime })

    // Tap "yeah" @6.0 — a 4s gap after the held "ohhh".
    currentTime = 6.0
    rerender({ seg: segment, time: currentTime })
    tap(result)
    rerender({ seg: segment, time: currentTime })

    const ohhh = segment.words[0]
    // The held release (~2.0) is preserved; NOT stretched toward "yeah" (6.0).
    expect(ohhh.end_time as number).toBeCloseTo(2.0, 1)
  })

  it('does not crush a held word against the STALE start of a not-yet-resynced next word', () => {
    // Real bug (job 3ecab928 seg 28): re-syncing a segment whose "call"/"seems" were adjacent
    // (seems.start ≈ 155.50, right after call). Holding "call" then advancing must NOT let the
    // overlap safety-net pull call.end back to seems' OLD start (155.50 → crushed to ~0.01s),
    // because "seems" hasn't been re-tapped yet. The held duration must survive.
    const initial = makeSegment([
      w('a', 'call', 155.4, 155.5), // old: 0.1s
      w('b', 'seems', 155.5, 155.56), // old start 155.5, right after call (STALE once we advance)
    ])

    let segment = initial
    let currentTime = 0

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

    const mk = (type: 'keydown' | 'keyup') =>
      ({ type, code: 'Space', preventDefault: () => {}, stopPropagation: () => {} } as unknown as KeyboardEvent)

    // HOLD "call": press at playhead 155.30, release at 155.60 after >200ms held.
    const nowSpy = jest.spyOn(Date, 'now')
    currentTime = 155.3
    rerender({ seg: segment, time: currentTime })
    nowSpy.mockReturnValue(10_000)
    act(() => result.current.handleSpacebar(mk('keydown')))
    currentTime = 155.6
    rerender({ seg: segment, time: currentTime })
    nowSpy.mockReturnValue(10_400) // held 400ms > TAP_THRESHOLD_MS
    act(() => result.current.handleSpacebar(mk('keyup')))
    nowSpy.mockRestore()
    rerender({ seg: segment, time: currentTime })

    const call = segment.words[0]
    // The held release (~155.60) is preserved; NOT crushed to seems' stale start (~155.49).
    expect(call.end_time as number).toBeCloseTo(155.6, 1)
    expect(call.end_time as number).toBeGreaterThan(155.5)
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

  describe('a pass that stops before the last word', () => {
    // gen's fake fixture line (backend/services/fake_workers/lyrics.py): five words with OLD
    // timings 15.5..19.5; the next line starts at 20.0. Tapping only the first four words, later
    // than their old starts, leaves "transcribed" at a stale 18.74 BEFORE "ever" (19.6).
    const fixtureLine = () =>
      makeSegment([
        w('w0', 'Nothing', 15.5, 16.26),
        w('w1', 'here', 16.31, 17.07),
        w('w2', 'was', 17.12, 17.88),
        w('w3', 'ever', 17.93, 18.69),
        w('w4', 'transcribed', 18.74, 19.5),
      ])

    function tapFourWords() {
      let segment = fixtureLine()
      let currentTime = 15.0
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
      ;[16.0, 17.2, 18.4, 19.6].forEach((t) => {
        currentTime = t
        rerender({ seg: segment, time: currentTime })
        tap(result)
        rerender({ seg: segment, time: currentTime })
      })
      return {
        result,
        get segment() {
          return segment
        },
        // A fresh segment object re-runs the auto-stop effect, which checks immediately.
        rerender: (time: number) => rerender({ seg: { ...segment }, time }),
      }
    }

    function expectInOrderWithTranscribedUntimed(words: Word[]) {
      const transcribed = words[4]
      expect(transcribed.start_time).toBeNull()
      expect(transcribed.end_time).toBeNull()
      const timed = words.filter((x) => x.start_time !== null)
      expect(timed.map((x) => x.text)).toEqual(['Nothing', 'here', 'was', 'ever'])
      for (let i = 1; i < timed.length; i++) {
        expect(timed[i].start_time as number).toBeGreaterThanOrEqual(timed[i - 1].end_time as number)
      }
      expect(words[3].start_time).toBeCloseTo(19.6, 5)
    }

    it('clears the stale untapped word on auto-stop instead of leaving it out of order', () => {
      const h = tapFourWords()
      expect(h.result.current.isManualSyncing).toBe(true)
      // Playback runs on a full lead-out past the (grown) segment end, then auto-stops.
      window.isAudioPlaying = true
      h.rerender((h.segment.end_time as number) + 3.5)
      expect(h.result.current.isManualSyncing).toBe(false)
      expectInOrderWithTranscribedUntimed(h.segment.words)
    })

    it('clears the stale untapped word when the user stops the pass', () => {
      const h = tapFourWords()
      act(() => h.result.current.startManualSync()) // the Stop button toggles the pass off
      expect(h.result.current.isManualSyncing).toBe(false)
      expectInOrderWithTranscribedUntimed(h.segment.words)
    })

    it('keeps untapped words that still start after the last tapped word, capping the tapped end', () => {
      // Taps land EARLIER than the old timings: "transcribed" (18.74) is still after "ever" (18.3),
      // so it keeps its timing, and "ever" must end before it.
      let segment = fixtureLine()
      let currentTime = 15.0
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
      ;[15.4, 16.2, 17.0, 18.3].forEach((t) => {
        currentTime = t
        rerender({ seg: segment, time: currentTime })
        tap(result)
        rerender({ seg: segment, time: currentTime })
      })
      act(() => result.current.startManualSync())
      const [, , , ever, transcribed] = segment.words
      expect(transcribed.start_time).toBeCloseTo(18.74, 5)
      expect(transcribed.end_time).toBeCloseTo(19.5, 5)
      expect(ever.end_time as number).toBeLessThan(18.74)
      expect(ever.end_time as number).toBeGreaterThanOrEqual(ever.start_time as number)
    })
  })
})
