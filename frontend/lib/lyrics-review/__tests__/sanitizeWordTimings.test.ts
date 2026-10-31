import { sanitizeSegmentTimings } from '../sanitizeWordTimings'
import { LyricsSegment } from '../types'

// The exact shape that broke job 17f7c313.
function scalpSegment(): LyricsSegment {
  return {
    id: 's1',
    text: 'A whiskey and a beer, ...',
    start_time: 15.18,
    end_time: 18.04,
    words: [
      { id: 'a', text: 'A', start_time: 0, end_time: -0.005 },
      { id: 'b', text: 'whiskey', start_time: 0, end_time: -0.005 },
      { id: 'c', text: 'and', start_time: 0, end_time: 0 },
      { id: 'd', text: 'a', start_time: 0, end_time: 0 },
      { id: 'e', text: 'beer,', start_time: 16.1, end_time: 16.42 },
    ],
  }
}

describe('sanitizeSegmentTimings', () => {
  it('clamps out-of-bounds leading words into the segment window', () => {
    const { segment, changes } = sanitizeSegmentTimings(scalpSegment())
    for (const w of segment.words) {
      expect(w.start_time!).toBeGreaterThanOrEqual(15.18)
      expect(w.end_time!).toBeGreaterThanOrEqual(w.start_time!)
      expect(w.end_time!).toBeLessThanOrEqual(18.04 + 1e-9)
    }
    expect(changes.length).toBeGreaterThan(0)
    expect(changes.some((c) => c.wordId === 'a')).toBe(true)
  })

  it('leaves already-valid segments untouched and reports no changes', () => {
    const seg: LyricsSegment = {
      id: 's', text: 'Filling up that cup', start_time: 21.4, end_time: 22.78,
      words: [
        { id: 'w0', text: 'Filling', start_time: 21.4, end_time: 21.82 },
        { id: 'w1', text: 'up', start_time: 21.86, end_time: 22.1 },
        { id: 'w2', text: 'that', start_time: 22.14, end_time: 22.42 },
        { id: 'w3', text: 'cup', start_time: 22.46, end_time: 22.78 },
      ],
    }
    const { changes } = sanitizeSegmentTimings(seg)
    expect(changes).toEqual([])
  })

  it('handles null word timings by clamping to the segment start', () => {
    const seg: LyricsSegment = {
      id: 's', text: 'x y', start_time: 5, end_time: 6,
      words: [
        { id: 'w0', text: 'x', start_time: null, end_time: null },
        { id: 'w1', text: 'y', start_time: 5.5, end_time: 6 },
      ],
    }
    const { segment } = sanitizeSegmentTimings(seg)
    expect(segment.words[0].start_time!).toBeGreaterThanOrEqual(5)
    expect(segment.words[0].end_time!).toBeGreaterThanOrEqual(segment.words[0].start_time!)
  })

  it('places a word whose start is past the segment end at the previous word end, not collapsed', () => {
    const seg: LyricsSegment = {
      id: 's', text: 'x y', start_time: 10, end_time: 12,
      words: [
        { id: 'w0', text: 'x', start_time: 10, end_time: 11 },
        { id: 'w1', text: 'y', start_time: 99, end_time: 100 },
      ],
    }
    const { segment } = sanitizeSegmentTimings(seg)
    expect(segment.words[1].start_time!).toBe(11)
    expect(segment.words[1].end_time!).toBeGreaterThanOrEqual(11)
    expect(segment.words[1].end_time!).toBeLessThanOrEqual(12)
  })

  it('returns segment untouched when segment bounds are null (non-finite)', () => {
    const seg: LyricsSegment = {
      id: 's', text: 'hello world', start_time: null, end_time: null,
      words: [
        { id: 'w0', text: 'hello', start_time: 3, end_time: 4 },
        { id: 'w1', text: 'world', start_time: 4, end_time: 5 },
      ],
    }
    const { segment: out, changes } = sanitizeSegmentTimings(seg)
    expect(changes).toEqual([])
    expect(out.words[0].start_time).toBe(3)
    expect(out.words[0].end_time).toBe(4)
    expect(out.words[1].start_time).toBe(4)
    expect(out.words[1].end_time).toBe(5)
  })

  it('moves a word that starts before the previous word ends to after it (mirrors timing_sanitizer.py)', () => {
    // A Tap To Sync pass that stopped before the last word: "ever" re-tapped at 19.6, but
    // "transcribed" kept its stale 18.74 start, so it would highlight out of order.
    const seg: LyricsSegment = {
      id: 's', text: 'was ever transcribed', start_time: 18.4, end_time: 20.1,
      words: [
        { id: 'w2', text: 'was', start_time: 18.4, end_time: 19.3 },
        { id: 'w3', text: 'ever', start_time: 19.6, end_time: 20.1 },
        { id: 'w4', text: 'transcribed', start_time: 18.74, end_time: 19.5 },
      ],
    }
    const { segment, changes } = sanitizeSegmentTimings(seg)
    expect(segment.words[0]).toBe(seg.words[0])
    expect(segment.words[1]).toBe(seg.words[1])
    expect(segment.words[2].start_time).toBe(20.1)
    expect(segment.words[2].end_time).toBe(20.1)
    expect(changes.map((c) => c.wordText)).toEqual(['transcribed', 'transcribed'])
  })

  it('leaves a small overlap with the previous word untouched (common in AI transcriptions)', () => {
    const seg: LyricsSegment = {
      id: 's', text: 'a b', start_time: 1, end_time: 3,
      words: [
        { id: 'w0', text: 'a', start_time: 1, end_time: 2 },
        { id: 'w1', text: 'b', start_time: 1.98, end_time: 3 },
      ],
    }
    const { segment, changes } = sanitizeSegmentTimings(seg)
    expect(changes).toEqual([])
    expect(segment).toBe(seg)
  })
})
