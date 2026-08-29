import { renderHook, waitFor } from '@testing-library/react'
import { useAutoCorrect } from '@/hooks/useAutoCorrect'
import type { CorrectionData, EditLog } from '@/lib/lyrics-review/types'

// The network fetch must never fire in pre-applied mode.
const fetchSpy = jest.fn()
jest.mock('@/lib/api/autoCorrect', () => ({
  ...jest.requireActual('@/lib/api/autoCorrect'),
  fetchAutoCorrectSuggestions: (...args: unknown[]) => {
    fetchSpy(...args)
    return Promise.resolve({ suggestions: [], model: 'x', cached: false, elapsed_seconds: 0, settings_applied: {} })
  },
}))

function makeData(): CorrectionData {
  return {
    original_segments: [],
    reference_lyrics: { spotify: { segments: [] } as any },
    anchor_sequences: [],
    gap_sequences: [],
    resized_segments: [],
    corrections_made: 0,
    confidence: 1,
    corrections: [],
    corrected_segments: [{ id: 's0', text: 'not much here', words: [], start_time: 0, end_time: 1 }],
    metadata: {},
  } as unknown as CorrectionData
}

const editLog: EditLog = { session_id: 's', job_id: 'j', audio_hash: 'h', started_at: '', entries: [] }

const baseArgs = {
  jobId: 'j1',
  updateDataWithHistory: jest.fn(),
  editLog,
  getAuthToken: () => 'tok',
}

describe('useAutoCorrect pre-applied mode (C2)', () => {
  beforeEach(() => fetchSpy.mockClear())

  it('seeds applied/rejected decisions from the server without running the network call', async () => {
    const suggestions = [
      { id: 'a', op: 'replace', category: 'mishearing', word_ids: ['w0'], segment_ids: ['s0'],
        original_text: 'an', new_text: 'not', confidence: 0.9, models: ['m'], consensus: 1, total_models: 1 },
      { id: 'b', op: 'replace', category: 'grammar', word_ids: ['w1'], segment_ids: ['s0'],
        original_text: 'amateur', new_text: 'much', confidence: 0.9, models: ['m'], consensus: 1, total_models: 1 },
    ] as any

    const { result } = renderHook(() =>
      useAutoCorrect({
        ...baseArgs,
        data: makeData(),
        autoRunOnLoad: false,
        autoApplyOnLoad: false,
        preApplied: { suggestions, appliedIds: ['a'], rejectedIds: ['b'] },
      }),
    )

    await waitFor(() => expect(result.current.status).toBe('reviewing'))
    expect(result.current.isPreApplied).toBe(true)
    expect(result.current.suggestions).toHaveLength(2)
    expect(result.current.decisions['a']).toBe('accepted')
    expect(result.current.decisions['b']).toBe('rejected')
    expect(result.current.acceptedCount).toBe(1)
    // Crucially: no live LLM/network call in pre-applied mode.
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('does not seed when preApplied is null (normal mode stays idle without auto-run)', () => {
    const { result } = renderHook(() =>
      useAutoCorrect({
        ...baseArgs,
        data: makeData(),
        autoRunOnLoad: false,
        autoApplyOnLoad: false,
        preApplied: null,
      }),
    )
    expect(result.current.isPreApplied).toBe(false)
    expect(result.current.status).toBe('idle')
    expect(fetchSpy).not.toHaveBeenCalled()
  })
})
