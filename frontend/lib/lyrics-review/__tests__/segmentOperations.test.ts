import {
  splitSegment,
  mergeSegment,
  addSegmentBefore,
  deleteSegment,
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
