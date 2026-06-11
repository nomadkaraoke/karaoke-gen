import {
  applySuggestion,
  revertSuggestion,
  isSuggestionStale,
} from '../autoCorrectApply'
import type { LyricsSegment } from '../../types'
import type { AiSuggestion } from '@/lib/api/autoCorrect'

function makeSegments(): LyricsSegment[] {
  return [
    {
      id: 'seg-1',
      text: "Sippin' on straight glory",
      start_time: 10,
      end_time: 12,
      words: [
        { id: 'w0', text: "Sippin'", start_time: 10, end_time: 10.5 },
        { id: 'w1', text: 'on', start_time: 10.5, end_time: 11 },
        { id: 'w2', text: 'straight', start_time: 11, end_time: 11.5 },
        { id: 'w3', text: 'glory', start_time: 11.5, end_time: 12 },
      ],
    },
    {
      id: 'seg-2',
      text: 'oh oh oh',
      start_time: 13,
      end_time: 14.5,
      words: [
        { id: 'w4', text: 'oh', start_time: 13, end_time: 13.5 },
        { id: 'w5', text: 'oh', start_time: 13.5, end_time: 14 },
        { id: 'w6', text: 'oh', start_time: 14, end_time: 14.5 },
      ],
    },
  ]
}

function suggestion(partial: Partial<AiSuggestion>): AiSuggestion {
  return {
    id: 'sug-1',
    op: 'replace',
    word_ids: ['w3'],
    segment_ids: ['seg-1'],
    original_text: 'glory',
    new_text: 'chlorine',
    reason: 'reference match',
    category: 'mishearing',
    confidence: 0.95,
    ...partial,
  }
}

describe('applySuggestion', () => {
  it('replaces a single word and rebuilds segment text', () => {
    const result = applySuggestion(makeSegments(), suggestion({}))
    expect(result).not.toBeNull()
    const seg = result!.segments[0]
    expect(seg.text).toBe("Sippin' on straight chlorine")
    expect(seg.words).toHaveLength(4)
    const newWord = seg.words[3]
    expect(newWord.text).toBe('chlorine')
    expect(newWord.id).not.toBe('w3')
    expect(newWord.start_time).toBe(11.5)
    expect(newWord.end_time).toBe(12)
    expect(newWord.created_during_correction).toBe(true)
    expect(result!.undo.newWordIds).toEqual([newWord.id])
    expect(result!.undo.removedWords.map((w) => w.id)).toEqual(['w3'])
  })

  it('replaces a multi-word span with a different word count', () => {
    const result = applySuggestion(
      makeSegments(),
      suggestion({
        word_ids: ['w2', 'w3'],
        original_text: 'straight glory',
        new_text: 'straight-up chlorine gas',
      }),
    )
    const seg = result!.segments[0]
    expect(seg.words.map((w) => w.text)).toEqual([
      "Sippin'",
      'on',
      'straight-up',
      'chlorine',
      'gas',
    ])
    // timings distributed across the original 11→12 span
    expect(seg.words[2].start_time).toBeCloseTo(11)
    expect(seg.words[4].end_time).toBeCloseTo(12)
  })

  it('does not mutate the input segments', () => {
    const input = makeSegments()
    applySuggestion(input, suggestion({}))
    expect(input[0].words[3].text).toBe('glory')
    expect(input[0].text).toBe("Sippin' on straight glory")
  })

  it('deletes words and rebuilds text', () => {
    const result = applySuggestion(
      makeSegments(),
      suggestion({
        op: 'delete',
        word_ids: ['w4', 'w5'],
        segment_ids: ['seg-2'],
        original_text: 'oh oh',
        new_text: '',
      }),
    )
    const seg = result!.segments[1]
    expect(seg.words.map((w) => w.text)).toEqual(['oh'])
    expect(seg.text).toBe('oh')
  })

  it('removes the segment entirely when a delete empties it', () => {
    const result = applySuggestion(
      makeSegments(),
      suggestion({
        op: 'delete',
        word_ids: ['w4', 'w5', 'w6'],
        segment_ids: ['seg-2'],
        original_text: 'oh oh oh',
        new_text: '',
      }),
    )
    expect(result!.segments).toHaveLength(1)
    expect(result!.undo.removedSegment).not.toBeNull()
    expect(result!.undo.removedSegment!.index).toBe(1)
  })

  it('inserts words after the anchor', () => {
    const result = applySuggestion(
      makeSegments(),
      suggestion({
        op: 'insert_after',
        word_ids: ['w1'],
        original_text: '',
        new_text: 'the',
      }),
    )
    const seg = result!.segments[0]
    expect(seg.words.map((w) => w.text)).toEqual([
      "Sippin'",
      'on',
      'the',
      'straight',
      'glory',
    ])
    // inserted word sits between anchor end (11) and next word start (11)
    expect(seg.words[2].start_time).toBeGreaterThanOrEqual(11 - 1e-9)
  })

  it('returns null for stale suggestions (missing word ids)', () => {
    expect(
      applySuggestion(makeSegments(), suggestion({ word_ids: ['gone'] })),
    ).toBeNull()
  })

  it('returns null for non-contiguous word ids', () => {
    expect(
      applySuggestion(makeSegments(), suggestion({ word_ids: ['w1', 'w3'] })),
    ).toBeNull()
  })
})

describe('revertSuggestion', () => {
  it('round-trips a replace', () => {
    const original = makeSegments()
    const applied = applySuggestion(original, suggestion({}))!
    const reverted = revertSuggestion(applied.segments, applied.undo)!
    expect(reverted[0].words.map((w) => w.id)).toEqual(['w0', 'w1', 'w2', 'w3'])
    expect(reverted[0].text).toBe("Sippin' on straight glory")
  })

  it('round-trips a delete', () => {
    const applied = applySuggestion(
      makeSegments(),
      suggestion({
        op: 'delete',
        word_ids: ['w5'],
        segment_ids: ['seg-2'],
        original_text: 'oh',
        new_text: '',
      }),
    )!
    const reverted = revertSuggestion(applied.segments, applied.undo)!
    expect(reverted[1].words.map((w) => w.id)).toEqual(['w4', 'w5', 'w6'])
  })

  it('round-trips a whole-segment delete', () => {
    const applied = applySuggestion(
      makeSegments(),
      suggestion({
        op: 'delete',
        word_ids: ['w4', 'w5', 'w6'],
        segment_ids: ['seg-2'],
        original_text: 'oh oh oh',
        new_text: '',
      }),
    )!
    const reverted = revertSuggestion(applied.segments, applied.undo)!
    expect(reverted).toHaveLength(2)
    expect(reverted[1].id).toBe('seg-2')
    expect(reverted[1].words).toHaveLength(3)
  })

  it('round-trips an insert_after', () => {
    const applied = applySuggestion(
      makeSegments(),
      suggestion({
        op: 'insert_after',
        word_ids: ['w1'],
        original_text: '',
        new_text: 'the real',
      }),
    )!
    const reverted = revertSuggestion(applied.segments, applied.undo)!
    expect(reverted[0].words.map((w) => w.id)).toEqual(['w0', 'w1', 'w2', 'w3'])
  })

  it('returns null when the applied words were since removed', () => {
    const applied = applySuggestion(makeSegments(), suggestion({}))!
    const tampered = applied.segments.map((s) => ({
      ...s,
      words: s.words.filter((w) => !applied.undo.newWordIds.includes(w.id)),
    }))
    expect(revertSuggestion(tampered, applied.undo)).toBeNull()
  })
})

describe('isSuggestionStale', () => {
  it('is false for intact targets', () => {
    expect(isSuggestionStale(makeSegments(), suggestion({}))).toBe(false)
  })

  it('is true after the target word was edited away', () => {
    const segments = makeSegments()
    segments[0].words = segments[0].words.filter((w) => w.id !== 'w3')
    expect(isSuggestionStale(segments, suggestion({}))).toBe(true)
  })
})
