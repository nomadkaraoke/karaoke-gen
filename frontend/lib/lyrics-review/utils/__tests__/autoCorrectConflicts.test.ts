import {
  getConflictSiblings,
  pickAcceptAllWinners,
} from '../autoCorrectConflicts'
import type { AiSuggestion } from '@/lib/api/autoCorrect'

function sugg(partial: Partial<AiSuggestion>): AiSuggestion {
  return {
    id: 'x',
    op: 'replace',
    word_ids: ['w1'],
    segment_ids: ['s1'],
    original_text: 'a',
    new_text: 'b',
    reason: '',
    category: 'mishearing',
    confidence: 0.9,
    models: ['m1'],
    consensus: 1,
    total_models: 2,
    conflict_group: null,
    ...partial,
  }
}

describe('getConflictSiblings', () => {
  const list = [
    sugg({ id: 'a', conflict_group: 'g1' }),
    sugg({ id: 'b', conflict_group: 'g1' }),
    sugg({ id: 'c', conflict_group: 'g2' }),
    sugg({ id: 'd', conflict_group: null }),
  ]

  it('returns other members of the same group', () => {
    expect(getConflictSiblings(list, 'a').map((s) => s.id)).toEqual(['b'])
  })

  it('returns empty for ungrouped or unknown ids', () => {
    expect(getConflictSiblings(list, 'd')).toEqual([])
    expect(getConflictSiblings(list, 'zzz')).toEqual([])
  })
})

describe('pickAcceptAllWinners', () => {
  it('keeps all non-conflicting suggestions', () => {
    const list = [sugg({ id: 'a' }), sugg({ id: 'b' })]
    expect(pickAcceptAllWinners(list)).toEqual(['a', 'b'])
  })

  it('picks highest consensus within a group', () => {
    const list = [
      sugg({ id: 'a', conflict_group: 'g', consensus: 1, confidence: 0.99 }),
      sugg({ id: 'b', conflict_group: 'g', consensus: 2, confidence: 0.6 }),
      sugg({ id: 'c' }),
    ]
    expect(pickAcceptAllWinners(list)).toEqual(['b', 'c'])
  })

  it('breaks consensus ties by confidence', () => {
    const list = [
      sugg({ id: 'a', conflict_group: 'g', consensus: 1, confidence: 0.7 }),
      sugg({ id: 'b', conflict_group: 'g', consensus: 1, confidence: 0.9 }),
    ]
    expect(pickAcceptAllWinners(list)).toEqual(['b'])
  })

  it('handles multiple independent groups', () => {
    const list = [
      sugg({ id: 'a', conflict_group: 'g1', consensus: 2 }),
      sugg({ id: 'b', conflict_group: 'g1', consensus: 1 }),
      sugg({ id: 'c', conflict_group: 'g2', consensus: 1, confidence: 0.5 }),
      sugg({ id: 'd', conflict_group: 'g2', consensus: 1, confidence: 0.8 }),
    ]
    expect(pickAcceptAllWinners(list)).toEqual(['a', 'd'])
  })
})
