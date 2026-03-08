import { renderHook, act } from '@testing-library/react'
import { useReviewSessionAutoSave } from '../use-review-session-autosave'
import type { CorrectionData } from '@/lib/lyrics-review/types'
import type { LyricsReviewApiClient } from '@/lib/api'

// Minimal CorrectionData factory
function makeCorrectionData(overrides: Partial<CorrectionData> = {}): CorrectionData {
  return {
    original_segments: [],
    reference_lyrics: {},
    anchor_sequences: [],
    gap_sequences: [],
    resized_segments: [],
    corrections_made: 0,
    confidence: 1,
    corrections: [],
    corrected_segments: [],
    metadata: {
      anchor_sequences_count: 0,
      gap_sequences_count: 0,
      total_words: 0,
      correction_ratio: 0,
    },
    correction_steps: [],
    ...overrides,
  }
}

function makeSegment(words: string[]) {
  return {
    id: `seg-${Math.random()}`,
    text: words.join(' '),
    words: words.map((text) => ({
      id: `w-${Math.random()}`,
      text,
      start_time: 0,
      end_time: 1,
      confidence: 1,
    })),
    start_time: 0,
    end_time: 1,
  }
}

function makeMockApiClient(overrides: Partial<LyricsReviewApiClient> = {}): LyricsReviewApiClient {
  return {
    submitCorrections: jest.fn(),
    submitAnnotations: jest.fn(),
    submitEditLog: jest.fn(),
    updateHandlers: jest.fn(),
    addLyrics: jest.fn(),
    getAudioUrl: jest.fn(),
    generatePreviewVideo: jest.fn(),
    getPreviewVideoUrl: jest.fn(),
    completeReview: jest.fn(),
    saveReviewSession: jest.fn().mockResolvedValue({ status: 'saved', session_id: 'test-session' }),
    listReviewSessions: jest.fn().mockResolvedValue({ sessions: [] }),
    getReviewSession: jest.fn(),
    deleteReviewSession: jest.fn(),
    ...overrides,
  }
}

describe('useReviewSessionAutoSave', () => {
  beforeEach(() => {
    jest.useFakeTimers()
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it('does not save when historyLength <= 1 (no edits)', async () => {
    const apiClient = makeMockApiClient()
    const { result } = renderHook(() =>
      useReviewSessionAutoSave({
        jobId: 'job-1',
        data: makeCorrectionData(),
        initialData: makeCorrectionData(),
        historyLength: 1,
        isReadOnly: false,
        apiClient,
      })
    )

    await act(async () => {
      await result.current.saveSession('manual')
    })

    expect(apiClient.saveReviewSession).not.toHaveBeenCalled()
  })

  it('saves when historyLength > 1 and trigger is manual', async () => {
    const apiClient = makeMockApiClient()
    const { result } = renderHook(() =>
      useReviewSessionAutoSave({
        jobId: 'job-1',
        data: makeCorrectionData(),
        initialData: makeCorrectionData(),
        historyLength: 5,
        isReadOnly: false,
        apiClient,
      })
    )

    await act(async () => {
      await result.current.saveSession('manual')
    })

    expect(apiClient.saveReviewSession).toHaveBeenCalledTimes(1)
    expect(apiClient.saveReviewSession).toHaveBeenCalledWith(
      expect.any(Object),
      4, // historyLength - 1
      'manual',
      expect.objectContaining({
        total_segments: expect.any(Number),
        corrections_made: expect.any(Number),
      })
    )
  })

  it('does not save again if historyLength has not changed since last save', async () => {
    const apiClient = makeMockApiClient()
    const { result } = renderHook(() =>
      useReviewSessionAutoSave({
        jobId: 'job-1',
        data: makeCorrectionData(),
        initialData: makeCorrectionData(),
        historyLength: 3,
        isReadOnly: false,
        apiClient,
      })
    )

    await act(async () => {
      await result.current.saveSession('manual')
    })
    expect(apiClient.saveReviewSession).toHaveBeenCalledTimes(1)

    // Second call with same historyLength should be skipped
    await act(async () => {
      await result.current.saveSession('manual')
    })
    expect(apiClient.saveReviewSession).toHaveBeenCalledTimes(1)
  })

  it('does not save in read-only mode (no interval set)', () => {
    const apiClient = makeMockApiClient()
    renderHook(() =>
      useReviewSessionAutoSave({
        jobId: 'job-1',
        data: makeCorrectionData(),
        initialData: makeCorrectionData(),
        historyLength: 5,
        isReadOnly: true,
        apiClient,
      })
    )

    // Advance timer past 60s
    act(() => {
      jest.advanceTimersByTime(120_000)
    })

    expect(apiClient.saveReviewSession).not.toHaveBeenCalled()
  })

  it('auto-saves on 60-second interval', async () => {
    const apiClient = makeMockApiClient()
    renderHook(() =>
      useReviewSessionAutoSave({
        jobId: 'job-1',
        data: makeCorrectionData(),
        initialData: makeCorrectionData(),
        historyLength: 5,
        isReadOnly: false,
        apiClient,
      })
    )

    // Advance to 60s
    await act(async () => {
      jest.advanceTimersByTime(60_000)
    })

    expect(apiClient.saveReviewSession).toHaveBeenCalledTimes(1)
    expect(apiClient.saveReviewSession).toHaveBeenCalledWith(
      expect.any(Object),
      4,
      'auto',
      expect.any(Object)
    )
  })

  it('handles save failure gracefully without throwing', async () => {
    const consoleWarn = jest.spyOn(console, 'warn').mockImplementation()
    const apiClient = makeMockApiClient({
      saveReviewSession: jest.fn().mockRejectedValue(new Error('Network error')),
    })
    const { result } = renderHook(() =>
      useReviewSessionAutoSave({
        jobId: 'job-1',
        data: makeCorrectionData(),
        initialData: makeCorrectionData(),
        historyLength: 3,
        isReadOnly: false,
        apiClient,
      })
    )

    await act(async () => {
      await result.current.saveSession('manual')
    })

    expect(consoleWarn).toHaveBeenCalledWith(
      'Review session auto-save failed:',
      expect.any(Error)
    )
    consoleWarn.mockRestore()
  })

  it('computes correct summary with word changes', async () => {
    const apiClient = makeMockApiClient()
    const initialData = makeCorrectionData({
      corrected_segments: [makeSegment(['hello', 'world', 'foo'])],
    })
    const currentData = makeCorrectionData({
      corrected_segments: [makeSegment(['hallo', 'world', 'bar'])],
    })

    const { result } = renderHook(() =>
      useReviewSessionAutoSave({
        jobId: 'job-1',
        data: currentData,
        initialData,
        historyLength: 3,
        isReadOnly: false,
        apiClient,
      })
    )

    await act(async () => {
      await result.current.saveSession('manual')
    })

    const summary = (apiClient.saveReviewSession as jest.Mock).mock.calls[0][3]
    expect(summary.total_segments).toBe(1)
    expect(summary.total_words).toBe(3)
    expect(summary.corrections_made).toBe(2)
    expect(summary.changed_words).toHaveLength(2)
    expect(summary.changed_words[0]).toEqual(
      expect.objectContaining({ original: 'hello', corrected: 'hallo', segment_index: 0 })
    )
  })

  it('caps changed_words at 50', async () => {
    const apiClient = makeMockApiClient()
    // Create segments with 60 different words
    const initialWords = Array.from({ length: 60 }, (_, i) => `word${i}`)
    const currentWords = Array.from({ length: 60 }, (_, i) => `changed${i}`)
    const initialData = makeCorrectionData({
      corrected_segments: [makeSegment(initialWords)],
    })
    const currentData = makeCorrectionData({
      corrected_segments: [makeSegment(currentWords)],
    })

    const { result } = renderHook(() =>
      useReviewSessionAutoSave({
        jobId: 'job-1',
        data: currentData,
        initialData,
        historyLength: 2,
        isReadOnly: false,
        apiClient,
      })
    )

    await act(async () => {
      await result.current.saveSession('manual')
    })

    const summary = (apiClient.saveReviewSession as jest.Mock).mock.calls[0][3]
    expect(summary.corrections_made).toBe(60)
    expect(summary.changed_words).toHaveLength(50) // Capped at 50
  })

  it('saves with preview trigger', async () => {
    const apiClient = makeMockApiClient()
    const { result } = renderHook(() =>
      useReviewSessionAutoSave({
        jobId: 'job-1',
        data: makeCorrectionData(),
        initialData: makeCorrectionData(),
        historyLength: 10,
        isReadOnly: false,
        apiClient,
      })
    )

    await act(async () => {
      await result.current.saveSession('preview')
    })

    expect(apiClient.saveReviewSession).toHaveBeenCalledWith(
      expect.any(Object),
      9,
      'preview',
      expect.any(Object)
    )
  })

  it('does not update lastBackupHistoryLength on non-saved/skipped status', async () => {
    const apiClient = makeMockApiClient({
      saveReviewSession: jest.fn().mockResolvedValue({ status: 'error' }),
    })
    const { result } = renderHook(() =>
      useReviewSessionAutoSave({
        jobId: 'job-1',
        data: makeCorrectionData(),
        initialData: makeCorrectionData(),
        historyLength: 5,
        isReadOnly: false,
        apiClient,
      })
    )

    await act(async () => {
      await result.current.saveSession('manual')
    })
    // Should still try on second call since first didn't save
    await act(async () => {
      await result.current.saveSession('manual')
    })

    expect(apiClient.saveReviewSession).toHaveBeenCalledTimes(2)
  })
})
