import { useCallback, useEffect, useRef } from 'react'
import type { CorrectionData } from '@/lib/lyrics-review/types'
import type { LyricsReviewApiClient, ReviewSessionSummary } from '@/lib/api'

interface UseReviewSessionAutoSaveOptions {
  jobId: string
  data: CorrectionData
  initialData: CorrectionData
  historyLength: number
  isReadOnly: boolean
  apiClient: LyricsReviewApiClient
}

function computeSummary(initialData: CorrectionData, currentData: CorrectionData): ReviewSessionSummary {
  const changedWords: ReviewSessionSummary['changed_words'] = []

  // Compare corrected_segments to find changed words
  const initialSegments = initialData.corrected_segments || []
  const currentSegments = currentData.corrected_segments || []

  let totalWords = 0
  for (let si = 0; si < currentSegments.length && si < initialSegments.length; si++) {
    const initialWords = initialSegments[si]?.words || []
    const currentWords = currentSegments[si]?.words || []
    totalWords += currentWords.length

    for (let wi = 0; wi < currentWords.length && wi < initialWords.length; wi++) {
      if (currentWords[wi].text !== initialWords[wi].text) {
        changedWords.push({
          original: initialWords[wi].text,
          corrected: currentWords[wi].text,
          segment_index: si,
        })
      }
    }
  }

  // Also count words in segments beyond the initial count
  for (let si = initialSegments.length; si < currentSegments.length; si++) {
    totalWords += (currentSegments[si]?.words || []).length
  }

  return {
    total_segments: currentSegments.length,
    total_words: totalWords || currentSegments.reduce((sum, s) => sum + (s.words?.length || 0), 0),
    corrections_made: changedWords.length,
    changed_words: changedWords.slice(0, 50), // Cap at 50 for storage
  }
}

const AUTO_SAVE_EVERY_N_EDITS = 3

export function useReviewSessionAutoSave({
  jobId,
  data,
  initialData,
  historyLength,
  isReadOnly,
  apiClient,
}: UseReviewSessionAutoSaveOptions) {
  const lastBackupHistoryLength = useRef(0)
  const isSaving = useRef(false)
  // Store latest values in refs so visibilitychange handler always has current data
  const dataRef = useRef(data)
  const initialDataRef = useRef(initialData)
  const historyLengthRef = useRef(historyLength)
  dataRef.current = data
  initialDataRef.current = initialData
  historyLengthRef.current = historyLength

  const doSave = useCallback(
    async (trigger: 'auto' | 'preview' | 'manual') => {
      const currentHistoryLength = historyLengthRef.current
      // No edits yet (only initial state in history)
      if (currentHistoryLength <= 1) return
      // No new edits since last backup
      if (currentHistoryLength === lastBackupHistoryLength.current) return
      // Already saving
      if (isSaving.current) return

      isSaving.current = true
      try {
        const summary = computeSummary(initialDataRef.current, dataRef.current)
        const result = await apiClient.saveReviewSession(
          dataRef.current,
          currentHistoryLength - 1,
          trigger,
          summary
        )
        if (result.status === 'saved' || result.status === 'skipped') {
          lastBackupHistoryLength.current = currentHistoryLength
        }
      } catch (err) {
        // Don't break the editing experience on backup failure
        console.warn('Review session auto-save failed:', err)
      } finally {
        isSaving.current = false
      }
    },
    [apiClient]
  )

  // Auto-save every N edits
  useEffect(() => {
    if (isReadOnly || historyLength <= 1) return
    const editsSinceBackup = historyLength - lastBackupHistoryLength.current
    if (editsSinceBackup >= AUTO_SAVE_EVERY_N_EDITS) {
      doSave('auto')
    }
  }, [historyLength, isReadOnly, doSave])

  // Save on page hide
  useEffect(() => {
    if (isReadOnly) return

    const handler = () => {
      if (document.visibilityState === 'hidden') {
        doSave('auto')
      }
    }

    document.addEventListener('visibilitychange', handler)
    return () => document.removeEventListener('visibilitychange', handler)
  }, [isReadOnly, doSave])

  // Expose saveSession that uses current refs
  const saveSession = useCallback(
    (trigger: 'auto' | 'preview' | 'manual') => doSave(trigger),
    [doSave]
  )

  return { saveSession }
}
