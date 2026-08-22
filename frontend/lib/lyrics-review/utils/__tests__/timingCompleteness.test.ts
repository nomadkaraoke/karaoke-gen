import { countUntimedWords, hasUntimedLyrics } from '../timingCompleteness'
import type { LyricsSegment } from '@/lib/lyrics-review/types'

function timedSegment(id = 's'): LyricsSegment {
  return {
    id,
    text: 'Hello world',
    start_time: 0,
    end_time: 1,
    words: [
      { id: `${id}-w0`, text: 'Hello', start_time: 0, end_time: 0.5 },
      { id: `${id}-w1`, text: 'world', start_time: 0.5, end_time: 1 },
    ],
  }
}

function untimedSegment(id = 'segment-0-1782928225732'): LyricsSegment {
  return {
    id,
    text: "Vor's veninde er blevet gift",
    start_time: null,
    end_time: null,
    words: [
      { id: `${id}-w0`, text: "Vor's", start_time: null, end_time: null },
      { id: `${id}-w1`, text: 'veninde', start_time: null, end_time: null },
    ],
  }
}

describe('countUntimedWords', () => {
  it('returns 0 for fully-timed lyrics', () => {
    expect(countUntimedWords([timedSegment('a'), timedSegment('b')])).toBe(0)
    expect(hasUntimedLyrics([timedSegment('a')])).toBe(false)
  })

  it('counts every untimed word (regression: job 231806a4 all-null)', () => {
    const segs = [untimedSegment('0'), untimedSegment('1')]
    expect(countUntimedWords(segs)).toBe(4)
    expect(hasUntimedLyrics(segs)).toBe(true)
  })

  it('counts a single untimed word among timed ones', () => {
    const seg = timedSegment('a')
    seg.words[1].start_time = null
    expect(countUntimedWords([seg])).toBe(1)
  })

  it('flags timed words whose segment bounds are null', () => {
    const seg = timedSegment('a')
    seg.start_time = null
    expect(countUntimedWords([seg])).toBe(2)
  })

  it('skips word-less segments', () => {
    const seg: LyricsSegment = { id: 's', text: '', start_time: null, end_time: null, words: [] }
    expect(countUntimedWords([seg])).toBe(0)
  })

  it('handles empty input', () => {
    expect(countUntimedWords([])).toBe(0)
  })
})
