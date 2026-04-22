import {
  cycleSinger,
  resolveSegmentSinger,
  collectSingersInUse,
  hasWordOverrides,
} from '../duet'
import type { LyricsSegment, Word, SingerId } from '../types'

const word = (id: string, singer?: SingerId): Word => ({
  id, text: id, start_time: 0, end_time: 1, singer,
})

const seg = (id: string, singer?: SingerId, words: Word[] = [word(`${id}-w1`)]): LyricsSegment => ({
  id, text: words.map(w => w.text).join(' '), words, start_time: 0, end_time: 1, singer,
})

describe('cycleSinger', () => {
  it('cycles 1 → 2 → 0 (Both) → 1', () => {
    expect(cycleSinger(1)).toBe(2)
    expect(cycleSinger(2)).toBe(0)
    expect(cycleSinger(0)).toBe(1)
  })

  it('undefined cycles to 2 (treated as Singer 1 default)', () => {
    expect(cycleSinger(undefined)).toBe(2)
  })
})

describe('resolveSegmentSinger', () => {
  it('returns 1 when segment.singer is undefined', () => {
    expect(resolveSegmentSinger(seg('s1', undefined))).toBe(1)
  })
  it('returns the explicit singer when set', () => {
    expect(resolveSegmentSinger(seg('s1', 2))).toBe(2)
    expect(resolveSegmentSinger(seg('s1', 0))).toBe(0)
  })
})

describe('collectSingersInUse', () => {
  it('returns [1] for solo (no singer fields)', () => {
    expect(collectSingersInUse([seg('s1')])).toEqual([1])
  })
  it('includes all used singers, sorted 1,2,0', () => {
    const segments = [
      seg('s1', 1),
      seg('s2', 2),
      seg('s3', 0),
    ]
    expect(collectSingersInUse(segments)).toEqual([1, 2, 0])
  })
  it('picks up word-level overrides', () => {
    const segments = [
      seg('s1', 1, [word('w1'), word('w2', 2)]),
    ]
    expect(collectSingersInUse(segments)).toEqual([1, 2])
  })
})

describe('hasWordOverrides', () => {
  it('false when no words have singer field', () => {
    expect(hasWordOverrides(seg('s1', 1, [word('w1'), word('w2')]))).toBe(false)
  })
  it('false when all word singers match segment', () => {
    expect(hasWordOverrides(seg('s1', 1, [word('w1', 1), word('w2', 1)]))).toBe(false)
  })
  it('true when any word singer differs from segment', () => {
    expect(hasWordOverrides(seg('s1', 1, [word('w1'), word('w2', 2)]))).toBe(true)
  })
})
