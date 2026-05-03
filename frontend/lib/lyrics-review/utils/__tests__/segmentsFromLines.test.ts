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

  it('uses redistributedTiming when provided, producing segments equal to lines.length', () => {
    const existing = [
      make('one', [{ text: 'one', start: 0, end: 1 }]),
      make('two', [{ text: 'two', start: 1, end: 2 }]),
      make('three', [{ text: 'three', start: 2, end: 3 }]),
    ]
    const timing: Array<[number, number]> = [[0, 1], [1, 2], [2, 3]]
    const result = segmentsFromLines(['ONE', 'TWO', 'THREE'], existing, { redistributedTiming: timing })

    expect(result).toHaveLength(3)
    expect(result[0].text).toBe('ONE')
    expect(result[0].start_time).toBe(0)
    expect(result[0].end_time).toBe(1)
    expect(result[2].text).toBe('THREE')
    expect(result[2].start_time).toBe(2)
    expect(result[2].end_time).toBe(3)
    // All word timings should fall within segment bounds
    result.forEach((seg) => {
      seg.words.forEach((w) => {
        expect(w.start_time).toBeGreaterThanOrEqual(seg.start_time ?? 0)
        expect(w.end_time).toBeLessThanOrEqual(seg.end_time ?? 0)
      })
    })
  })

  it('redistributedTiming supports variable line count (more lines than existingSegments)', () => {
    const existing = [
      make('hello world', [
        { text: 'hello', start: 0, end: 1 },
        { text: 'world', start: 1, end: 2 },
      ]),
    ]
    // AI returned 3 lines with redistribution timing
    const timing: Array<[number, number]> = [[0, 0.6], [0.6, 1.3], [1.3, 2.0]]
    const result = segmentsFromLines(['hello', 'world', 'again'], existing, { redistributedTiming: timing })

    expect(result).toHaveLength(3)
    expect(result[0].text).toBe('hello')
    expect(result[1].text).toBe('world')
    expect(result[2].text).toBe('again')
    // Segment 2 uses a generated id since existingSegments[2] doesn't exist
    expect(result[2].id).toMatch(/seg-new-2/)
    expect(result[2].start_time).toBeCloseTo(1.3)
    expect(result[2].end_time).toBeCloseTo(2.0)
  })
})
