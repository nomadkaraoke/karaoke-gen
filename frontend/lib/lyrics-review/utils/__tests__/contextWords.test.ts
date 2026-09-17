import { computeContextWordsBySegment } from '../contextWords'
import { LyricsSegment, Word } from '../../types'

const word = (id: string, text: string, start: number | null, end: number | null): Word => ({
  id,
  text,
  start_time: start,
  end_time: end,
  confidence: 1,
})

const segment = (id: string, words: Word[]): LyricsSegment => {
  const starts = words.map((w) => w.start_time).filter((n): n is number => n !== null)
  const ends = words.map((w) => w.end_time).filter((n): n is number => n !== null)
  return {
    id,
    text: words.map((w) => w.text).join(' '),
    words,
    start_time: starts.length ? Math.min(...starts) : null,
    end_time: ends.length ? Math.max(...ends) : null,
  }
}

describe('computeContextWordsBySegment', () => {
  it('includes neighbour words that fall within the padded window', () => {
    const segments = [
      segment('s0', [word('a', 'hello', 0, 1), word('b', 'world', 1, 2)]),
      segment('s1', [word('c', 'again', 2.5, 3.5)]),
    ]

    const map = computeContextWordsBySegment(segments, 1)

    // s1's window is [1.5, 4.5]; s0's "world" (1..2) intersects it.
    const forS1 = map.get(1)!.map((w) => w.id)
    expect(forS1).toContain('b')
    // s0's "hello" (0..1) is outside [1.5, 4.5]
    expect(forS1).not.toContain('a')
  })

  it('excludes the segment’s own words', () => {
    const segments = [segment('s0', [word('a', 'hi', 0, 1), word('b', 'there', 1, 2)])]
    const map = computeContextWordsBySegment(segments, 1)
    expect(map.get(0)).toEqual([])
  })

  it('excludes neighbours outside the padded window', () => {
    const segments = [
      segment('s0', [word('a', 'early', 0, 1)]),
      segment('s1', [word('b', 'late', 10, 11)]),
    ]
    const map = computeContextWordsBySegment(segments, 1)
    expect(map.get(0)).toEqual([])
    expect(map.get(1)).toEqual([])
  })

  it('returns an empty array for a segment with no timed bounds', () => {
    const segments = [
      segment('s0', [word('a', 'timed', 0, 1)]),
      segment('s1', [word('b', 'untimed', null, null)]),
    ]
    const map = computeContextWordsBySegment(segments, 1)
    expect(map.get(1)).toEqual([])
  })

  it('skips untimed neighbour words', () => {
    const segments = [
      segment('s0', [word('a', 'anchor', 2, 3)]),
      segment('s1', [word('b', 'ghost', null, null), word('c', 'real', 3.2, 3.8)]),
    ]
    const map = computeContextWordsBySegment(segments, 1)
    const forS0 = map.get(0)!.map((w) => w.id)
    expect(forS0).toContain('c')
    expect(forS0).not.toContain('b')
  })
})
