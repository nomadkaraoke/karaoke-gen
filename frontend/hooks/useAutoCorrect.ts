'use client'

/**
 * State machine for the opt-in AI auto-correct suggestions feature.
 *
 * Nothing runs unless the reviewer explicitly triggers it; suggestions are a
 * pending layer that never mutates the working data until individually (or
 * bulk) accepted. Every accept/reject/undo lands in the edit log so we can
 * measure real-world accept rates per category over time.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  AiSuggestion,
  AutoCorrectApiError,
  AutoCorrectSettings,
  DEFAULT_AUTO_CORRECT_SETTINGS,
  fetchAutoCorrectSuggestions,
} from '@/lib/api/autoCorrect'
import {
  applySuggestion,
  revertSuggestion,
  isSuggestionStale,
  SuggestionUndoInfo,
} from '@/lib/lyrics-review/utils/autoCorrectApply'
import { addEditEntry } from '@/lib/lyrics-review/utils/editLog'
import type { CorrectionData, EditLog } from '@/lib/lyrics-review/types'

export type SuggestionDecision = 'pending' | 'accepted' | 'rejected' | 'stale'
export type AutoCorrectStatus = 'idle' | 'running' | 'reviewing' | 'error'

interface UseAutoCorrectArgs {
  jobId?: string
  data: CorrectionData
  updateDataWithHistory: (newData: CorrectionData, desc?: string) => void
  editLog: EditLog
  getAuthToken: () => string | undefined
}

export function useAutoCorrect({
  jobId,
  data,
  updateDataWithHistory,
  editLog,
  getAuthToken,
}: UseAutoCorrectArgs) {
  const [status, setStatus] = useState<AutoCorrectStatus>('idle')
  const [suggestions, setSuggestions] = useState<AiSuggestion[]>([])
  const [decisions, setDecisions] = useState<Record<string, SuggestionDecision>>({})
  const [model, setModel] = useState<string>('')
  const [error, setError] = useState<string | null>(null)
  const undoInfos = useRef<Record<string, SuggestionUndoInfo>>({})
  const abortRef = useRef<AbortController | null>(null)

  const hasReferences = Object.keys(data.reference_lyrics ?? {}).length > 0

  // Abort any in-flight request on unmount so no state updates land after.
  useEffect(() => {
    return () => {
      abortRef.current?.abort()
    }
  }, [])

  const run = useCallback(
    async (settings: AutoCorrectSettings = DEFAULT_AUTO_CORRECT_SETTINGS) => {
      if (!jobId) return
      setStatus('running')
      setError(null)
      abortRef.current?.abort()
      const ac = new AbortController()
      abortRef.current = ac
      try {
        const response = await fetchAutoCorrectSuggestions(
          jobId,
          {
            segments: data.corrected_segments,
            referenceLyrics: data.reference_lyrics ?? {},
            artist: data.metadata?.artist,
            title: data.metadata?.title,
            settings,
          },
          ac.signal,
          getAuthToken(),
        )
        setSuggestions(response.suggestions)
        setDecisions(
          Object.fromEntries(response.suggestions.map((s) => [s.id, 'pending'])),
        )
        undoInfos.current = {}
        setModel(response.model)
        setStatus('reviewing')
        addEditEntry(editLog, 'ai_suggestion_run', {
          details: {
            model: response.model,
            suggestion_count: response.suggestions.length,
            elapsed_seconds: response.elapsed_seconds,
            settings: response.settings_applied as unknown as Record<string, unknown>,
          },
        })
      } catch (err) {
        if ((err as Error).name === 'AbortError') {
          setStatus('idle')
          return
        }
        const message =
          err instanceof AutoCorrectApiError
            ? err.message
            : 'Unexpected error generating suggestions'
        setError(message)
        setStatus('error')
      }
    },
    [jobId, data, editLog, getAuthToken],
  )

  const cancel = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  const logDecision = useCallback(
    (s: AiSuggestion, op: 'ai_suggestion_accept' | 'ai_suggestion_reject' | 'ai_suggestion_undo') => {
      addEditEntry(editLog, op, {
        segment_id: s.segment_ids[0] ?? null,
        word_ids_before: s.word_ids,
        text_before: s.original_text,
        text_after: s.new_text,
        details: {
          suggestion_id: s.id,
          op: s.op,
          category: s.category,
          confidence: s.confidence,
          reason: s.reason,
          model,
        },
      })
    },
    [editLog, model],
  )

  const accept = useCallback(
    (id: string) => {
      const s = suggestions.find((x) => x.id === id)
      if (!s || decisions[id] !== 'pending') return
      const result = applySuggestion(data.corrected_segments, s)
      if (!result) {
        setDecisions((d) => ({ ...d, [id]: 'stale' }))
        return
      }
      undoInfos.current[id] = result.undo
      updateDataWithHistory(
        { ...data, corrected_segments: result.segments },
        'accept AI suggestion',
      )
      setDecisions((d) => ({ ...d, [id]: 'accepted' }))
      logDecision(s, 'ai_suggestion_accept')
    },
    [suggestions, decisions, data, updateDataWithHistory, logDecision],
  )

  const reject = useCallback(
    (id: string) => {
      const s = suggestions.find((x) => x.id === id)
      if (!s || decisions[id] !== 'pending') return
      setDecisions((d) => ({ ...d, [id]: 'rejected' }))
      logDecision(s, 'ai_suggestion_reject')
    },
    [suggestions, decisions, logDecision],
  )

  const undoAccept = useCallback(
    (id: string): boolean => {
      const s = suggestions.find((x) => x.id === id)
      const undo = undoInfos.current[id]
      if (!s || decisions[id] !== 'accepted' || !undo) return false
      const reverted = revertSuggestion(data.corrected_segments, undo)
      if (!reverted) return false
      delete undoInfos.current[id]
      updateDataWithHistory(
        { ...data, corrected_segments: reverted },
        'undo AI suggestion',
      )
      setDecisions((d) => ({ ...d, [id]: 'pending' }))
      logDecision(s, 'ai_suggestion_undo')
      return true
    },
    [suggestions, decisions, data, updateDataWithHistory, logDecision],
  )

  const acceptAll = useCallback(() => {
    let segments = data.corrected_segments
    const newDecisions: Record<string, SuggestionDecision> = { ...decisions }
    let applied = 0
    for (const s of suggestions) {
      if (newDecisions[s.id] !== 'pending') continue
      const result = applySuggestion(segments, s)
      if (!result) {
        newDecisions[s.id] = 'stale'
        continue
      }
      segments = result.segments
      undoInfos.current[s.id] = result.undo
      newDecisions[s.id] = 'accepted'
      logDecision(s, 'ai_suggestion_accept')
      applied += 1
    }
    if (applied > 0) {
      updateDataWithHistory(
        { ...data, corrected_segments: segments },
        'accept all AI suggestions',
      )
    }
    setDecisions(newDecisions)
  }, [suggestions, decisions, data, updateDataWithHistory, logDecision])

  const rejectAll = useCallback(() => {
    const newDecisions: Record<string, SuggestionDecision> = { ...decisions }
    for (const s of suggestions) {
      if (newDecisions[s.id] !== 'pending') continue
      newDecisions[s.id] = 'rejected'
      logDecision(s, 'ai_suggestion_reject')
    }
    setDecisions(newDecisions)
  }, [suggestions, decisions, logDecision])

  const dismiss = useCallback(() => {
    abortRef.current?.abort()
    setStatus('idle')
    setSuggestions([])
    setDecisions({})
    undoInfos.current = {}
    setError(null)
  }, [])

  /** Recompute staleness for pending suggestions against the latest data. */
  const isPendingAndStale = useCallback(
    (s: AiSuggestion) =>
      decisions[s.id] === 'pending' &&
      isSuggestionStale(data.corrected_segments, s),
    [decisions, data.corrected_segments],
  )

  const pendingCount = suggestions.filter((s) => decisions[s.id] === 'pending').length
  const acceptedCount = suggestions.filter((s) => decisions[s.id] === 'accepted').length

  return {
    status,
    suggestions,
    decisions,
    model,
    error,
    hasReferences,
    pendingCount,
    acceptedCount,
    run,
    cancel,
    accept,
    reject,
    undoAccept,
    acceptAll,
    rejectAll,
    dismiss,
    isPendingAndStale,
  }
}

export type AutoCorrectController = ReturnType<typeof useAutoCorrect>
