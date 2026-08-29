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
import {
  getConflictSiblings,
  pickAcceptAllWinners,
} from '@/lib/lyrics-review/utils/autoCorrectConflicts'
import { addEditEntry } from '@/lib/lyrics-review/utils/editLog'
import type { CorrectionData, EditLog } from '@/lib/lyrics-review/types'

export type SuggestionDecision = 'pending' | 'accepted' | 'rejected' | 'stale'
export type AutoCorrectStatus = 'idle' | 'running' | 'reviewing' | 'error'

interface UseAutoCorrectArgs {
  jobId?: string
  data: CorrectionData
  updateDataWithHistory: (newData: CorrectionData, desc?: string) => void
  /** Commit data as the new review baseline (collapses undo history). Used for
   *  the on-load auto-apply so auto-corrections are the starting point, not a
   *  user "edit" that would trigger a save/restore prompt. */
  rebaseData?: (newData: CorrectionData) => void
  editLog: EditLog
  getAuthToken: () => string | undefined
  /** Auto-trigger one run on load (when references exist) so suggestions are
   *  ready without a click. Normally a cache hit (backend pre-generates). */
  autoRunOnLoad?: boolean
  /** After the auto-run completes, immediately apply all suggestions (the
   *  equivalent of clicking "Accept All") so the reviewer starts from the
   *  corrected lyrics. Implies autoRunOnLoad. */
  autoApplyOnLoad?: boolean
  /** Server-side pre-apply (C2): when the backend already applied the AI
   *  corrections before the review-ready notification, the working `data` is the
   *  final state. The hook then seeds the panel in read-only "already applied"
   *  mode and does NOT auto-run / auto-apply (no in-browser race). The caller
   *  passes autoRunOnLoad/autoApplyOnLoad = false in this case. */
  preApplied?: {
    suggestions: AiSuggestion[]
    appliedIds: string[]
    rejectedIds: string[]
  } | null
}

export function useAutoCorrect({
  jobId,
  data,
  updateDataWithHistory,
  rebaseData,
  editLog,
  getAuthToken,
  autoRunOnLoad = false,
  autoApplyOnLoad = false,
  preApplied = null,
}: UseAutoCorrectArgs) {
  const [status, setStatus] = useState<AutoCorrectStatus>('idle')
  const [suggestions, setSuggestions] = useState<AiSuggestion[]>([])
  const [decisions, setDecisions] = useState<Record<string, SuggestionDecision>>({})
  const [model, setModel] = useState<string>('')
  const [cached, setCached] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const undoInfos = useRef<Record<string, SuggestionUndoInfo>>({})
  const abortRef = useRef<AbortController | null>(null)
  // Guards the one-shot auto-run on load. Reset on abort so a request that was
  // cancelled mid-flight (e.g. React StrictMode's dev double-invoke, or a fast
  // re-render) re-fires instead of permanently stranding the auto-run.
  const autoRanRef = useRef(false)

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
        setCached(response.cached)
        setStatus('reviewing')
        addEditEntry(editLog, 'ai_suggestion_run', {
          details: {
            model: response.model,
            cached: response.cached,
            suggestion_count: response.suggestions.length,
            elapsed_seconds: response.elapsed_seconds,
            settings: response.settings_applied as unknown as Record<string, unknown>,
          },
        })
      } catch (err) {
        if ((err as Error).name === 'AbortError') {
          // Allow the load effect to retry: flipping status back to 'idle' is a
          // dependency change that re-runs it, and the guard is now clear.
          autoRanRef.current = false
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

  // Fire exactly one run on load once references + segments are available.
  // The ref guard ensures it never re-fires as `data` changes during review
  // (but is cleared on abort above so a cancelled attempt can retry).
  useEffect(() => {
    if (!(autoRunOnLoad || autoApplyOnLoad) || autoRanRef.current) return
    if (!jobId || status !== 'idle') return
    if (!hasReferences || !(data.corrected_segments?.length > 0)) return
    autoRanRef.current = true
    void run()
  }, [autoRunOnLoad, autoApplyOnLoad, jobId, status, hasReferences, data.corrected_segments, run])

  // Pre-applied mode (C2): the backend already applied the corrections and the
  // working `data` is the final state. Seed the panel to show what was applied
  // (read-only) WITHOUT running the network call or re-applying. One-shot.
  const isPreApplied = Boolean(preApplied)
  const preAppliedSeededRef = useRef(false)
  useEffect(() => {
    if (!preApplied || preAppliedSeededRef.current) return
    if (status !== 'idle') return
    preAppliedSeededRef.current = true
    const applied = new Set(preApplied.appliedIds)
    const seeded: Record<string, SuggestionDecision> = {}
    for (const s of preApplied.suggestions) {
      seeded[s.id] = applied.has(s.id) ? 'accepted' : 'rejected'
    }
    setSuggestions(preApplied.suggestions)
    setDecisions(seeded)
    setCached(true)
    setStatus('reviewing')
  }, [preApplied, status])

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
          models: s.models,
          consensus: s.consensus,
          total_models: s.total_models,
          conflict_group: s.conflict_group,
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
      // Accepting one variant of a conflict group rejects the others —
      // they target the same words with a different outcome.
      const siblingUpdates: Record<string, SuggestionDecision> = {}
      for (const sibling of getConflictSiblings(suggestions, id)) {
        if (decisions[sibling.id] === 'pending') {
          siblingUpdates[sibling.id] = 'rejected'
          logDecision(sibling, 'ai_suggestion_reject')
        }
      }
      setDecisions((d) => ({ ...d, ...siblingUpdates, [id]: 'accepted' }))
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

  const acceptAll = useCallback(
    (asBaseline = false) => {
      let segments = data.corrected_segments
      const newDecisions: Record<string, SuggestionDecision> = { ...decisions }
      // In conflict groups only the winner (highest consensus, then
      // confidence) is applied; the losing variants are rejected.
      const winners = new Set(
        pickAcceptAllWinners(suggestions.filter((s) => newDecisions[s.id] === 'pending')),
      )
      let applied = 0
      for (const s of suggestions) {
        if (newDecisions[s.id] !== 'pending') continue
        if (!winners.has(s.id)) {
          newDecisions[s.id] = 'rejected'
          logDecision(s, 'ai_suggestion_reject')
          continue
        }
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
        const next = { ...data, corrected_segments: segments }
        // On-load auto-apply commits as the review baseline (no undo entry, no
        // save/restore prompt); a manual "Accept all" is a normal edit.
        if (asBaseline && rebaseData) {
          rebaseData(next)
        } else {
          updateDataWithHistory(next, 'accept all AI suggestions')
        }
      }
      setDecisions(newDecisions)
    },
    [suggestions, decisions, data, updateDataWithHistory, rebaseData, logDecision],
  )

  // Revert every applied auto-correction back to the original transcription
  // words (in one undo step), leaving any manual edits to other words intact.
  const revertAll = useCallback(() => {
    let segments = data.corrected_segments
    const newDecisions: Record<string, SuggestionDecision> = { ...decisions }
    let reverted = 0
    // Reverse order: later applies may sit after earlier ones in a segment.
    for (let i = suggestions.length - 1; i >= 0; i--) {
      const s = suggestions[i]
      if (decisions[s.id] !== 'accepted') continue
      const undo = undoInfos.current[s.id]
      if (!undo) continue
      const result = revertSuggestion(segments, undo)
      if (!result) continue
      segments = result
      delete undoInfos.current[s.id]
      newDecisions[s.id] = 'pending'
      logDecision(s, 'ai_suggestion_undo')
      reverted += 1
    }
    if (reverted > 0) {
      updateDataWithHistory(
        { ...data, corrected_segments: segments },
        'revert all AI suggestions',
      )
    }
    setDecisions(newDecisions)
  }, [suggestions, decisions, data, updateDataWithHistory, logDecision])

  // Auto-apply: once the auto-run lands, apply every suggestion exactly as
  // "Accept All" would, once. The ref guard survives the data/decisions churn
  // that acceptAll triggers (each re-creates this callback), and re-runs on a
  // reload no-op because the re-fetched suggestions are stale → 0 pending.
  const autoAppliedRef = useRef(false)
  useEffect(() => {
    if (!autoApplyOnLoad || autoAppliedRef.current) return
    if (status !== 'reviewing') return
    const hasPending = suggestions.some((s) => decisions[s.id] === 'pending')
    if (!hasPending) return
    autoAppliedRef.current = true
    acceptAll(true)
  }, [autoApplyOnLoad, status, suggestions, decisions, acceptAll])

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
    cached,
    error,
    hasReferences,
    isPreApplied,
    pendingCount,
    acceptedCount,
    run,
    cancel,
    accept,
    reject,
    undoAccept,
    acceptAll,
    revertAll,
    rejectAll,
    dismiss,
    isPendingAndStale,
  }
}

export type AutoCorrectController = ReturnType<typeof useAutoCorrect>
