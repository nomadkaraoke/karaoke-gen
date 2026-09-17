import { classifyWord, barClassForWord, buildSegmentDecorations } from '../wordDecorations'
import { CorrectionData, LyricsSegment } from '../../types'

const baseData = (overrides: Partial<CorrectionData> = {}): CorrectionData =>
  ({
    corrected_segments: [],
    corrections: [],
    anchor_sequences: [],
    gap_sequences: [],
    reference_lyrics: {},
    ...overrides,
  } as unknown as CorrectionData)

describe('classifyWord', () => {
  it('classifies an anchor word', () => {
    const data = baseData({
      anchor_sequences: [{ transcribed_word_ids: ['w1'] }] as never,
    })
    expect(classifyWord(data, 'w1')).toEqual({ category: 'anchor', isCorrected: false })
  })

  it('classifies a gap word via transcribed ids', () => {
    const data = baseData({
      gap_sequences: [{ transcribed_word_ids: ['w2'], reference_word_ids: {} }] as never,
    })
    expect(classifyWord(data, 'w2')).toEqual({ category: 'gap', isCorrected: false })
  })

  it('flags a corrected word', () => {
    const data = baseData({
      corrections: [{ word_id: 'w3', corrected_word_id: 'w3', source: 's', handler: 'h' }] as never,
      gap_sequences: [{ transcribed_word_ids: ['w3'], reference_word_ids: {} }] as never,
    })
    expect(classifyWord(data, 'w3')).toEqual({ category: 'gap', isCorrected: true })
  })

  it('returns other when neither anchor nor gap', () => {
    expect(classifyWord(baseData(), 'w9')).toEqual({ category: 'other', isCorrected: false })
  })

  it('anchor takes priority over gap', () => {
    const data = baseData({
      anchor_sequences: [{ transcribed_word_ids: ['w4'] }] as never,
      gap_sequences: [{ transcribed_word_ids: ['w4'], reference_word_ids: {} }] as never,
    })
    expect(classifyWord(data, 'w4').category).toBe('anchor')
  })
})

describe('barClassForWord', () => {
  const flags = {
    category: 'other' as const,
    isCorrected: false,
    isUserEdited: false,
    isAiCorrected: false,
  }

  it('AI-corrected wins → purple', () => {
    expect(barClassForWord({ ...flags, category: 'anchor', isAiCorrected: true })).toContain('purple')
  })
  it('anchor → blue', () => {
    expect(barClassForWord({ ...flags, category: 'anchor' })).toContain('blue')
  })
  it('user-edited → lime', () => {
    expect(barClassForWord({ ...flags, isUserEdited: true })).toContain('lime')
  })
  it('corrected gap → green', () => {
    expect(barClassForWord({ ...flags, category: 'gap', isCorrected: true })).toContain('green')
  })
  it('uncorrected gap → orange', () => {
    expect(barClassForWord({ ...flags, category: 'gap' })).toContain('orange')
  })
  it('plain word → faint neutral', () => {
    expect(barClassForWord(flags)).toBe('bg-foreground/10')
  })
})

describe('buildSegmentDecorations', () => {
  const segment: LyricsSegment = {
    id: 's0',
    text: 'a b',
    start_time: 0,
    end_time: 2,
    words: [
      { id: 'w1', text: 'a', start_time: 0, end_time: 1, confidence: 1 },
      { id: 'w2', text: 'b', start_time: 1, end_time: 2, confidence: 1 },
    ],
  }

  it('maps each word to a bar class and includes AI ghost text only for AI words', () => {
    const data = baseData({
      anchor_sequences: [{ transcribed_word_ids: ['w1'] }] as never,
    })
    const map = buildSegmentDecorations(data, segment, {
      aiCorrectedWordIds: new Set(['w2']),
      aiOriginalTextByWordId: new Map([['w2', 'original']]),
    })
    expect(map.get('w1')!.barClassName).toContain('blue')
    expect(map.get('w1')!.originalText).toBeUndefined()
    expect(map.get('w2')!.barClassName).toContain('purple')
    expect(map.get('w2')!.originalText).toBe('original')
  })
})
