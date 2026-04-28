import { segmentsFromLines } from '../segmentsFromLines'
import type { LyricsSegment } from '@/lib/lyrics-review/types'

const make = (text: string, words: { text: string; start: number | null; end: number | null }[]): LyricsSegment => ({
  id: `seg-${text}`,
  text,
  start_time: words[0]?.start ?? null,
  end_time: words[words.length - 1]?.end ?? null,
  words: words.map((w, i) => ({
    id: `w-${text}-${i}`,
    text: w.text,
    start_time: w.start,
    end_time: w.end,
    confidence: 1,
  })),
})

describe('segmentsFromLines', () => {
  it('returns deep-copies of unchanged segments', () => {
    const existing = [
      make('hello world', [
        { text: 'hello', start: 0, end: 1 },
        { text: 'world', start: 1, end: 2 },
      ]),
    ]
    const result = segmentsFromLines(['hello world'], existing)
    expect(result).toHaveLength(1)
    expect(result[0].text).toBe('hello world')
    expect(result[0]).not.toBe(existing[0])
    expect(result[0].words[0].start_time).toBe(0)
  })

  it('preserves per-word timing when text changes but word count matches', () => {
    const existing = [
      make('hello world', [
        { text: 'hello', start: 0, end: 1 },
        { text: 'world', start: 1, end: 2 },
      ]),
    ]
    const result = segmentsFromLines(['howdy partner'], existing)
    expect(result[0].text).toBe('howdy partner')
    expect(result[0].words.map((w) => w.text)).toEqual(['howdy', 'partner'])
    expect(result[0].words[0].start_time).toBe(0)
    expect(result[0].words[1].end_time).toBe(2)
  })

  it('distributes timing when word count changes', () => {
    const existing = [
      make('one two', [
        { text: 'one', start: 0, end: 1 },
        { text: 'two', start: 1, end: 2 },
      ]),
    ]
    const result = segmentsFromLines(['one two three'], existing)
    expect(result[0].text).toBe('one two three')
    expect(result[0].words.map((w) => w.text)).toEqual(['one', 'two', 'three'])
    // distributed timings should fall in the segment range
    result[0].words.forEach((w) => {
      expect(w.start_time).not.toBeNull()
      expect(w.end_time).not.toBeNull()
    })
  })

  it('iterates over existingSegments.length even if input has fewer lines', () => {
    const existing = [
      make('a', [{ text: 'a', start: 0, end: 1 }]),
      make('b', [{ text: 'b', start: 1, end: 2 }]),
    ]
    const result = segmentsFromLines(['a'], existing)
    // Second segment becomes empty text
    expect(result).toHaveLength(2)
    expect(result[1].text).toBe('')
  })
})
