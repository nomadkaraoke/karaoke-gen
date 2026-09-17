import { recomputeSegmentFromWords, resolveInitialViewMode } from '../segmentTiming'
import { LyricsSegment, Word } from '../../types'

const word = (id: string, text: string, start: number | null, end: number | null): Word => ({
  id,
  text,
  start_time: start,
  end_time: end,
  confidence: 1,
})

const baseSegment: LyricsSegment = {
  id: 's0',
  text: 'old text',
  words: [],
  start_time: 0,
  end_time: 1,
}

describe('recomputeSegmentFromWords', () => {
  it('recomputes start/end from the min/max of word timings', () => {
    const words = [word('a', 'No', 140.6, 141.0), word('b', 'looking', 141.0, 142.3)]
    const result = recomputeSegmentFromWords(baseSegment, words)
    expect(result.start_time).toBeCloseTo(140.6)
    expect(result.end_time).toBeCloseTo(142.3)
  })

  it('rebuilds the joined text from the words', () => {
    const words = [word('a', 'No', 0, 1), word('b', 'looking', 1, 2), word('c', 'back', 2, 3)]
    const result = recomputeSegmentFromWords(baseSegment, words)
    expect(result.text).toBe('No looking back')
  })

  it('is not affected by the order timings appear in (uses min/max, not first/last)', () => {
    const words = [word('a', 'late', 5, 6), word('b', 'early', 1, 2)]
    const result = recomputeSegmentFromWords(baseSegment, words)
    expect(result.start_time).toBe(1)
    expect(result.end_time).toBe(6)
  })

  it('yields null bounds when no word is timed', () => {
    const words = [word('a', 'x', null, null)]
    const result = recomputeSegmentFromWords(baseSegment, words)
    expect(result.start_time).toBeNull()
    expect(result.end_time).toBeNull()
  })

  it('preserves other segment fields', () => {
    const seg = { ...baseSegment, id: 'keep-me', singer: 2 as const }
    const result = recomputeSegmentFromWords(seg, [word('a', 'hi', 0, 1)])
    expect(result.id).toBe('keep-me')
    expect((result as { singer?: number }).singer).toBe(2)
  })
})

describe('resolveInitialViewMode', () => {
  const store = (data: Record<string, string>) => ({
    getItem: (k: string) => (k in data ? data[k] : null),
  })

  it('defaults to simple when no storage', () => {
    expect(resolveInitialViewMode(null)).toBe('simple')
  })

  it('returns the stored enum value', () => {
    expect(resolveInitialViewMode(store({ lyricsReviewViewMode: 'waveforms' }))).toBe('waveforms')
    expect(resolveInitialViewMode(store({ lyricsReviewViewMode: 'advanced' }))).toBe('advanced')
    expect(resolveInitialViewMode(store({ lyricsReviewViewMode: 'simple' }))).toBe('simple')
  })

  it('migrates the legacy advanced boolean', () => {
    expect(resolveInitialViewMode(store({ lyricsReviewAdvancedMode: 'true' }))).toBe('advanced')
    expect(resolveInitialViewMode(store({ lyricsReviewAdvancedMode: 'false' }))).toBe('simple')
  })

  it('prefers the new key over the legacy boolean', () => {
    expect(
      resolveInitialViewMode(
        store({ lyricsReviewViewMode: 'waveforms', lyricsReviewAdvancedMode: 'true' })
      )
    ).toBe('waveforms')
  })

  it('ignores an unrecognised stored value', () => {
    expect(resolveInitialViewMode(store({ lyricsReviewViewMode: 'garbage' }))).toBe('simple')
  })
})
