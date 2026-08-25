import {
  splitSegment,
  mergeSegment,
  addSegmentBefore,
  deleteSegment,
  deleteWord,
} from '../utils/segmentOperations'
import type { CorrectionData } from '../types'

const baseData = (segments: any[]): CorrectionData => ({
  corrected_segments: segments,
  anchor_sequences: [],
  gap_sequences: [],
} as unknown as CorrectionData)

describe('segmentOperations — singer inheritance', () => {
  it('split: both halves inherit parent singer', () => {
    const data = baseData([{
      id: 's1', text: 'hello world foo', start_time: 0, end_time: 3,
      words: [
        { id: 'w1', text: 'hello', start_time: 0, end_time: 1 },
        { id: 'w2', text: 'world', start_time: 1, end_time: 2 },
        { id: 'w3', text: 'foo', start_time: 2, end_time: 3 },
      ],
      singer: 2,
    }])
    const result = splitSegment(data, 0, 0)  // split after word index 0 → two segments
    if (!result) throw new Error('split returned null')
    expect(result.corrected_segments[0].singer).toBe(2)
    expect(result.corrected_segments[1].singer).toBe(2)
  })

  it('split: word-level overrides stay with the half containing that word', () => {
    const data = baseData([{
      id: 's1', text: 'hello world', start_time: 0, end_time: 2,
      words: [
        { id: 'w1', text: 'hello', start_time: 0, end_time: 1, singer: 2 },
        { id: 'w2', text: 'world', start_time: 1, end_time: 2 },
      ],
      singer: 1,
    }])
    const result = splitSegment(data, 0, 0)
    if (!result) throw new Error('split returned null')
    // First half: w1 with singer=2 override; segment singer inherited as 1
    expect(result.corrected_segments[0].words[0].singer).toBe(2)
    // Second half: w2 with no override
    expect(result.corrected_segments[1].words[0].singer).toBeUndefined()
  })

  it('merge: result takes first segment singer; words preserve their singers', () => {
    const data = baseData([
      { id: 's1', text: 'hello', start_time: 0, end_time: 1,
        words: [{ id: 'w1', text: 'hello', start_time: 0, end_time: 1 }], singer: 1 },
      { id: 's2', text: 'world', start_time: 1, end_time: 2,
        words: [{ id: 'w2', text: 'world', start_time: 1, end_time: 2 }], singer: 2 },
    ])
    const result = mergeSegment(data, 0, true)
    expect(result.corrected_segments[0].singer).toBe(1)
    // The merged words — w2 was implicitly singer 2 via its segment,
    // it now becomes an explicit word-level override relative to the new segment singer (1)
    const mergedWords = result.corrected_segments[0].words
    expect(mergedWords.find((w: any) => w.id === 'w2')?.singer).toBe(2)
    expect(mergedWords.find((w: any) => w.id === 'w1')?.singer).toBeUndefined()
  })

  it('merge: promotes implicit-singer words when only the first segment has an explicit singer', () => {
    // First segment explicit singer=2, second segment has no singer (implicitly 1)
    // with implicit-singer words. Without resolving segment singers, w2 would
    // silently flip from singer 1 → singer 2 after merge. With the fix, w2
    // is explicitly pinned to singer 1 on merge so its hue survives.
    const data = baseData([
      { id: 's1', text: 'hello', start_time: 0, end_time: 1,
        words: [{ id: 'w1', text: 'hello', start_time: 0, end_time: 1 }], singer: 2 },
      { id: 's2', text: 'world', start_time: 1, end_time: 2,
        words: [{ id: 'w2', text: 'world', start_time: 1, end_time: 2 }] /* no singer set */ },
    ])
    const result = mergeSegment(data, 0, true)
    expect(result.corrected_segments[0].singer).toBe(2)
    const mergedWords = result.corrected_segments[0].words
    expect(mergedWords.find((w: any) => w.id === 'w1')?.singer).toBeUndefined()
    expect(mergedWords.find((w: any) => w.id === 'w2')?.singer).toBe(1)
  })

  it('addSegmentBefore: inherits next segment singer', () => {
    const data = baseData([
      { id: 's1', text: 'hello', start_time: 1, end_time: 2, words: [], singer: 2 },
    ])
    const result = addSegmentBefore(data, 0)
    expect(result.corrected_segments[0].singer).toBe(2)
  })

  it('addSegmentBefore: appends after the last segment without crashing (beforeIndex === length)', () => {
    // "Add segment after" on the final segment calls addSegmentBefore with an
    // index one past the end. Previously this dereferenced undefined and threw
    // "Cannot read properties of undefined (reading 'start_time')".
    const data = baseData([
      { id: 's1', text: 'hello', start_time: 3, end_time: 5, words: [], singer: 2 },
    ])
    const result = addSegmentBefore(data, 1)
    expect(result.corrected_segments).toHaveLength(2)
    // New segment is appended at the end...
    const appended = result.corrected_segments[1]
    expect(appended.id).not.toBe('s1')
    // ...starting just after the previous segment's end, and inheriting its singer.
    expect(appended.start_time).toBe(5)
    expect(appended.end_time).toBe(6)
    expect(appended.singer).toBe(2)
  })

  it('addSegmentBefore: appends into an empty segment list without crashing', () => {
    const data = baseData([])
    const result = addSegmentBefore(data, 0)
    expect(result.corrected_segments).toHaveLength(1)
    expect(result.corrected_segments[0].start_time).toBe(0)
    expect(result.corrected_segments[0].end_time).toBe(1)
    expect(result.corrected_segments[0].singer).toBeUndefined()
  })

  it('delete: remaining segments unchanged', () => {
    const data = baseData([
      { id: 's1', text: 'a', start_time: 0, end_time: 1, words: [], singer: 1 },
      { id: 's2', text: 'b', start_time: 1, end_time: 2, words: [], singer: 2 },
    ])
    const result = deleteSegment(data, 0)
    expect(result.corrected_segments[0].id).toBe('s2')
    expect(result.corrected_segments[0].singer).toBe(2)
  })
})

describe('deleteWord — segment timing recompute', () => {
  const threeWordSegment = () => baseData([{
    id: 's1', text: 'hello world foo', start_time: 0, end_time: 3,
    words: [
      { id: 'w1', text: 'hello', start_time: 0, end_time: 1 },
      { id: 'w2', text: 'world', start_time: 1, end_time: 2 },
      { id: 'w3', text: 'foo', start_time: 2, end_time: 3 },
    ],
  }])

  it('deleting the first word tightens segment start_time to the next word', () => {
    const result = deleteWord(threeWordSegment(), 'w1')
    const seg = result.corrected_segments[0]
    expect(seg.words.map((w: any) => w.id)).toEqual(['w2', 'w3'])
    expect(seg.start_time).toBe(1) // was 0 (w1's start); now w2's start
    expect(seg.end_time).toBe(3)
    expect(seg.text).toBe('world foo')
  })

  it('deleting the last word tightens segment end_time to the previous word', () => {
    const result = deleteWord(threeWordSegment(), 'w3')
    const seg = result.corrected_segments[0]
    expect(seg.words.map((w: any) => w.id)).toEqual(['w1', 'w2'])
    expect(seg.start_time).toBe(0)
    expect(seg.end_time).toBe(2) // was 3 (w3's end); now w2's end
    expect(seg.text).toBe('hello world')
  })

  it('deleting a middle word leaves segment bounds unchanged', () => {
    const result = deleteWord(threeWordSegment(), 'w2')
    const seg = result.corrected_segments[0]
    expect(seg.start_time).toBe(0)
    expect(seg.end_time).toBe(3)
  })

  it('deleting a word when all remaining words are untimed nulls the segment bounds', () => {
    const data = baseData([{
      id: 's1', text: 'hello world', start_time: 0, end_time: 1,
      words: [
        { id: 'w1', text: 'hello', start_time: 0, end_time: 1 },
        { id: 'w2', text: 'world', start_time: null, end_time: null },
      ],
    }])
    const result = deleteWord(data, 'w1')
    const seg = result.corrected_segments[0]
    expect(seg.start_time).toBeNull()
    expect(seg.end_time).toBeNull()
  })

  it('deleting the only word removes the whole segment', () => {
    const data = baseData([
      { id: 's1', text: 'hello', start_time: 0, end_time: 1,
        words: [{ id: 'w1', text: 'hello', start_time: 0, end_time: 1 }] },
      { id: 's2', text: 'world', start_time: 1, end_time: 2,
        words: [{ id: 'w2', text: 'world', start_time: 1, end_time: 2 }] },
    ])
    const result = deleteWord(data, 'w1')
    expect(result.corrected_segments.map((s: any) => s.id)).toEqual(['s2'])
  })
})
