'use client'

import { useTranslations } from 'next-intl'
import { useState, useEffect, useCallback, useMemo, useRef, memo } from 'react'
import { useRouter } from '@/i18n/routing'
import { Button } from '@/components/ui/button'
import { Video, Check, CheckCircle2 } from 'lucide-react'
import { toast } from 'sonner'
import {
  AnchorSequence,
  CorrectionData,
  GapSequence,
  HighlightInfo,
  InteractionMode,
  LyricsSegment,
  ReferenceSource,
  WordCorrection,
  FlashType,
  Word,
  WordClickInfo,
  ModalContent,
  SearchLyricsResponse,
} from '@/lib/lyrics-review/types'
import type { EditLog, EditLogEntry, EditFeedbackReason } from '@/lib/lyrics-review/types'
import type { InstrumentalSelectionType, LyricsReviewApiClient } from '@/lib/api'
import { lyricsReviewApi, warmupEncodingWorker, heartbeatEncodingWorker } from '@/lib/api'
import { cn } from '@/lib/utils'
import ReferenceView from './ReferenceView'
import TranscriptionView from './TranscriptionView'
import { EditModal } from './modals'
import { ReviewChangesModal } from './modals'
import { ReplaceAllLyricsModal } from './modals'
import { AddLyricsModal } from './modals'
import { FindReplaceModal } from './modals'
import { TimingOffsetModal } from './modals'
import CorrectionDetailCard from './CorrectionDetailCard'
import EditFeedbackBar from './EditFeedbackBar'
import {
  addSegmentBefore,
  splitSegment,
  deleteSegment,
  mergeSegment,
  findAndReplace,
  deleteWord,
} from '@/lib/lyrics-review/utils/segmentOperations'
import { loadSavedData, saveData, clearSavedData } from '@/lib/lyrics-review/utils/localStorage'
import {
  setupKeyboardHandlers,
  setModalHandler,
  getModalState,
} from '@/lib/lyrics-review/utils/keyboardHandlers'
import { createEditLog, addEditEntry, attachFeedback } from '@/lib/lyrics-review/utils/editLog'
import { useReviewSessionAutoSave } from '@/hooks/use-review-session-autosave'
import { hasMultipleSingers } from '@/lib/lyrics-review/duet'
import SessionRestoreDialog from './SessionRestoreDialog'
import Header from './Header'
import GapNavigator from './GapNavigator'
import { getWordsFromIds } from '@/lib/lyrics-review/utils/wordUtils'
import { applyOffsetToCorrectionData, applyOffsetToSegment } from '@/lib/lyrics-review/utils/timingUtils'

// Add type for window augmentation
declare global {
  interface Window {
    toggleAudioPlayback?: () => void
    seekAndPlayAudio?: (startTime: number) => void
    getAudioDuration?: () => number
    isAudioPlaying?: boolean
  }
}

interface ApiClient {
  submitCorrections: (data: CorrectionData, isDuet?: boolean) => Promise<void>
  submitAnnotations: (annotations: never[]) => Promise<void>
  submitEditLog: (editLog: EditLog) => Promise<void>
  updateHandlers: (handlers: string[]) => Promise<CorrectionData>
  addLyrics: (source: string, lyrics: string) => Promise<CorrectionData>
  searchLyrics?: (artist: string, title: string, forceSources?: string[]) => Promise<SearchLyricsResponse>
  getAudioUrl: (hash: string) => string
  generatePreviewVideo: (data: CorrectionData, isDuet?: boolean) => Promise<{
    status: string
    message?: string
    preview_hash?: string
  }>
  getPreviewVideoUrl: (hash: string) => string
  completeReview: () => Promise<{ status: string; job_status: string; message: string }>
  // Review session methods (optional - available in cloud mode)
  saveReviewSession?: LyricsReviewApiClient['saveReviewSession']
  listReviewSessions?: LyricsReviewApiClient['listReviewSessions']
  getReviewSession?: LyricsReviewApiClient['getReviewSession']
  deleteReviewSession?: LyricsReviewApiClient['deleteReviewSession']
}

export interface LyricsAnalyzerProps {
  data: CorrectionData
  onFileLoad: () => void
  onShowMetadata: () => void
  apiClient: ApiClient | null
  isReadOnly: boolean
  audioHash: string
  isLocalMode?: boolean
  jobId?: string
  hasExistingInstrumental?: boolean
}

export default function LyricsAnalyzer({
  data: initialData,
  onFileLoad,
  apiClient,
  isReadOnly,
  audioHash,
  isLocalMode = false,
  jobId,
  hasExistingInstrumental = false,
}: LyricsAnalyzerProps) {
  const t = useTranslations('lyricsReview')
  const router = useRouter()
  const isMobile = typeof window !== 'undefined' && window.innerWidth < 768

  // Modal state
  const [modalContent, setModalContent] = useState<ModalContent | null>(null)
  const [flashingType, setFlashingType] = useState<FlashType>(null)
  const [highlightInfo, setHighlightInfo] = useState<HighlightInfo | null>(null)
  const [currentSource, setCurrentSource] = useState<string>(() => {
    if (!initialData?.reference_lyrics) return ''
    const availableSources = Object.keys(initialData.reference_lyrics)
    return availableSources.length > 0 ? availableSources[0] : ''
  })
  const [isReviewComplete, setIsReviewComplete] = useState(false)
  const [originalData] = useState(() => JSON.parse(JSON.stringify(initialData)))
  const [interactionMode, setInteractionMode] = useState<InteractionMode>('edit')
  const [isShiftPressed, setIsShiftPressed] = useState(false)
  const [isCtrlPressed, setIsCtrlPressed] = useState(false)
  const [editModalSegment, setEditModalSegment] = useState<{
    segment: LyricsSegment
    index: number
    originalSegment: LyricsSegment
  } | null>(null)
  const [isReplaceAllLyricsModalOpen, setIsReplaceAllLyricsModalOpen] = useState(false)
  const [isReviewModalOpen, setIsReviewModalOpen] = useState(false)
  const [currentAudioTime, setCurrentAudioTime] = useState(0)
  const [isUpdatingHandlers, setIsUpdatingHandlers] = useState(false)
  const [flashingHandler, setFlashingHandler] = useState<string | null>(null)
  const [isAddingLyrics, setIsAddingLyrics] = useState(false)
  const [isAddLyricsModalOpen, setIsAddLyricsModalOpen] = useState(false)
  const [isAnyModalOpen, setIsAnyModalOpen] = useState(false)
  const [isFindReplaceModalOpen, setIsFindReplaceModalOpen] = useState(false)
  const [isTimingOffsetModalOpen, setIsTimingOffsetModalOpen] = useState(false)
  const [timingOffsetMs, setTimingOffsetMs] = useState(0)
  const [reviewMode, setReviewMode] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)

  // Success screen state (for auto-close after submission)
  const [showSuccess, setShowSuccess] = useState(false)
  const [countdown, setCountdown] = useState(2)
  const [showInstrumentalReview, setShowInstrumentalReview] = useState(false)

  // Edit log state
  const [editLog] = useState<EditLog>(() => createEditLog(jobId ?? 'unknown', audioHash))
  const [lastEditEntry, setLastEditEntry] = useState<EditLogEntry | null>(null)
  const [showFeedbackBar, setShowFeedbackBar] = useState(false)

  // Stats panel visibility
  const [statsVisible, setStatsVisible] = useState(false)

  // Session restore state
  const [sessionRestoreOpen, setSessionRestoreOpen] = useState(false)
  const [savedSessions, setSavedSessions] = useState<import('@/lib/api').ReviewSession[]>([])
  const [sessionsLoading, setSessionsLoading] = useState(false)

  // Advanced mode for transcription view (Simple/Advanced toggle)
  const [isDuet, setIsDuet] = useState<boolean>(
    (initialData as any).is_duet ?? false
  )

  const [focusedSegmentIndex, setFocusedSegmentIndex] = useState<number | null>(null)

  const [advancedMode, setAdvancedMode] = useState(() => {
    if (typeof window === 'undefined') return false
    return localStorage.getItem('lyricsReviewAdvancedMode') === 'true'
  })

  // Gap navigation state
  const [currentGapIndex, setCurrentGapIndex] = useState<number | null>(null)

  // Correction detail card state
  const [correctionDetailOpen, setCorrectionDetailOpen] = useState(false)
  const [selectedCorrection, setSelectedCorrection] = useState<{
    wordId: string
    originalWord: string
    correctedWord: string
    category: string | null
    confidence: number
    reason: string
    handler: string
    source: string
  } | null>(null)

  // State history for Undo/Redo
  const [history, setHistory] = useState<CorrectionData[]>([initialData])
  const [historyIndex, setHistoryIndex] = useState(0)

  // Derived state: the current data based on history index
  const data = history[historyIndex]

  // Function to update data and manage history
  const updateDataWithHistory = useCallback(
    (newData: CorrectionData, _actionDescription?: string) => {
      const newHistory = history.slice(0, historyIndex + 1)
      const deepCopiedNewData = JSON.parse(JSON.stringify(newData))
      newHistory.push(deepCopiedNewData)
      setHistory(newHistory)
      setHistoryIndex(newHistory.length - 1)
    },
    [history, historyIndex]
  )

  // Compute which word IDs have been edited by the user (compared to initial state)
  const editedWordIds = useMemo(() => {
    const initial = history[0]
    if (!initial || data === initial) return new Set<string>()
    // Build map of word ID → text from initial state
    const initialWordText = new Map<string, string>()
    for (const segment of initial.corrected_segments) {
      for (const word of segment.words) {
        initialWordText.set(word.id, word.text)
      }
    }
    const edited = new Set<string>()
    for (const segment of data.corrected_segments) {
      for (const word of segment.words) {
        const originalText = initialWordText.get(word.id)
        if (originalText === undefined) {
          // Word was added by the user
          edited.add(word.id)
        } else if (originalText !== word.text) {
          // Word text was changed
          edited.add(word.id)
        }
      }
    }
    return edited
  }, [data, history])

  // Reset history when initial data changes
  useEffect(() => {
    setHistory([initialData])
    setHistoryIndex(0)
  }, [initialData])

  // Apply restored data: replace history AND turn the duet toggle on if the
  // restored segments encode multi-singer assignments. We never turn duet
  // OFF here — a user who toggled duet mid-edit and then restored an early
  // solo snapshot probably still wants the toggle on for their next edit.
  const applyRestoredData = useCallback((restored: CorrectionData) => {
    setHistory([restored])
    setHistoryIndex(0)
    if (hasMultipleSingers(restored.corrected_segments || [])) {
      setIsDuet(true)
    }
  }, [])

  // Load saved sessions on mount - check server first, fall back to localStorage
  useEffect(() => {
    if (isReadOnly || !apiClient?.listReviewSessions) {
      // Local mode or read-only: use localStorage fallback
      const savedData = loadSavedData(initialData)
      if (savedData && typeof window !== 'undefined' && window.confirm('Found saved progress for this song. Would you like to restore it?')) {
        applyRestoredData(savedData)
      }
      return
    }

    // Cloud mode: check for server-side sessions
    let cancelled = false
    setSessionsLoading(true)
    apiClient.listReviewSessions().then(result => {
      if (cancelled) return
      setSessionsLoading(false)
      if (result.sessions.length > 0) {
        setSavedSessions(result.sessions)
        setSessionRestoreOpen(true)
      } else {
        // Fall back to localStorage if no server sessions
        const savedData = loadSavedData(initialData)
        if (savedData && typeof window !== 'undefined' && window.confirm('Found saved progress for this song. Would you like to restore it?')) {
          applyRestoredData(savedData)
        }
      }
    }).catch(() => {
      if (cancelled) return
      setSessionsLoading(false)
      // Fall back to localStorage on error
      const savedData = loadSavedData(initialData)
      if (savedData && typeof window !== 'undefined' && window.confirm('Found saved progress for this song. Would you like to restore it?')) {
        applyRestoredData(savedData)
      }
    })

    return () => { cancelled = true }
  }, [initialData, isReadOnly]) // eslint-disable-line react-hooks/exhaustive-deps

  // Handler for restoring a session from the dialog
  const handleSessionRestore = useCallback((correctionData: CorrectionData) => {
    applyRestoredData(correctionData)
  }, [applyRestoredData])

  // Handler to open session browser from toolbar
  const handleOpenSessionBrowser = useCallback(async () => {
    if (!apiClient?.listReviewSessions) return
    setSessionsLoading(true)
    try {
      const result = await apiClient.listReviewSessions()
      setSavedSessions(result.sessions)
    } catch {
      setSavedSessions([])
    }
    setSessionsLoading(false)
    setSessionRestoreOpen(true)
  }, [apiClient])

  // Warm up encoding worker VM when lyrics review page loads
  useEffect(() => {
    if (!isReadOnly) {
      warmupEncodingWorker()
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Debounced heartbeat — keeps VM alive during active editing
  const lastHeartbeat = useRef(0)
  const sendHeartbeat = useCallback(() => {
    const now = Date.now()
    if (now - lastHeartbeat.current > 60_000) {
      lastHeartbeat.current = now
      heartbeatEncodingWorker()
    }
  }, [])

  // Save data (localStorage - instant crash recovery) + heartbeat encoding worker
  useEffect(() => {
    if (!isReadOnly) {
      saveData(data, initialData)
      sendHeartbeat()
    }
  }, [data, isReadOnly, initialData, sendHeartbeat])

  // Server-side session auto-save (periodic backup)
  const { saveSession } = useReviewSessionAutoSave({
    jobId: jobId ?? '',
    data,
    initialData,
    historyLength: history.length,
    isReadOnly: isReadOnly || !apiClient?.saveReviewSession,
    apiClient: apiClient as LyricsReviewApiClient,
  })

  // Flash handler (defined early so gap navigation can use it)
  const handleFlash = useCallback((type: FlashType, info?: HighlightInfo) => {
    setFlashingType(null)
    setHighlightInfo(null)

    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        setFlashingType(type)
        if (info) {
          setHighlightInfo(info)
        }
        setTimeout(() => {
          setFlashingType(null)
          setHighlightInfo(null)
        }, 1200)
      })
    })
  }, [])

  // Compute uncorrected gaps for navigation
  const uncorrectedGaps = useMemo(() => {
    const gapCorrections = data.corrections.reduce(
      (map: Record<string, boolean>, correction) => {
        const gap = data.gap_sequences.find((g) =>
          g.transcribed_word_ids.includes(correction.word_id)
        )
        if (gap) map[gap.id] = true
        return map
      },
      {} as Record<string, boolean>
    )
    return data.gap_sequences.filter(
      (gap) => !gapCorrections[gap.id] && gap.transcribed_word_ids.length > 0
    )
  }, [data.gap_sequences, data.corrections])

  // Navigate to a specific gap
  const navigateToGap = useCallback(
    (index: number) => {
      if (index < 0 || index >= uncorrectedGaps.length) return
      setCurrentGapIndex(index)
      const gap = uncorrectedGaps[index]
      const firstWordId = gap.transcribed_word_ids[0]
      if (firstWordId) {
        const el = document.getElementById(`word-${firstWordId}`)
        if (el) {
          el.scrollIntoView({ behavior: 'smooth', block: 'center' })
        }
      }
      // Flash only the specific gap's words (not all uncorrected gaps)
      const gapWords = gap.transcribed_word_ids
        .map((id) => {
          for (const seg of data.corrected_segments) {
            const w = seg.words.find((w) => w.id === id)
            if (w) return w
          }
          return null
        })
        .filter((w): w is Word => w !== null)
      handleFlash('uncorrected', {
        type: 'gap',
        sequence: gap,
        transcribed_words: gapWords,
      })
    },
    [uncorrectedGaps, handleFlash, data.corrected_segments]
  )

  const handlePrevGap = useCallback(() => {
    const newIndex = currentGapIndex !== null ? currentGapIndex - 1 : 0
    navigateToGap(Math.max(0, newIndex))
  }, [currentGapIndex, navigateToGap])

  const handleNextGap = useCallback(() => {
    const newIndex = currentGapIndex !== null ? currentGapIndex + 1 : 0
    navigateToGap(Math.min(uncorrectedGaps.length - 1, newIndex))
  }, [currentGapIndex, uncorrectedGaps.length, navigateToGap])

  // Word IDs for the currently active gap (persistent ring indicator)
  const activeGapWordIds = useMemo(() => {
    if (currentGapIndex === null || currentGapIndex >= uncorrectedGaps.length) return undefined
    return new Set(uncorrectedGaps[currentGapIndex].transcribed_word_ids)
  }, [currentGapIndex, uncorrectedGaps])

  // Keyboard handlers
  useEffect(() => {
    const { handleKeyDown, handleKeyUp, cleanup } = setupKeyboardHandlers({
      setIsShiftPressed,
      setIsCtrlPressed,
      onNextGap: handleNextGap,
      onPrevGap: handlePrevGap,
      isDuet,
      focusedSegmentIndex,
      onAssignSegmentSinger: (idx, singer) => {
        const segments = [...data.corrected_segments]
        segments[idx] = { ...segments[idx], singer }
        updateDataWithHistory({ ...data, corrected_segments: segments }, 'singer change')
      },
    })

    window.addEventListener('keydown', handleKeyDown)
    window.addEventListener('keyup', handleKeyUp)

    if (isAnyModalOpen) {
      setIsShiftPressed(false)
      setIsCtrlPressed(false)
    }

    return () => {
      window.removeEventListener('keydown', handleKeyDown)
      window.removeEventListener('keyup', handleKeyUp)
      document.body.style.userSelect = ''
      cleanup()
    }
  }, [isAnyModalOpen, handleNextGap, handlePrevGap, isDuet, focusedSegmentIndex, data, updateDataWithHistory])

  // Update modal state tracking
  useEffect(() => {
    const modalOpen = Boolean(
      modalContent ||
        editModalSegment ||
        isReviewModalOpen ||
        isAddLyricsModalOpen ||
        isFindReplaceModalOpen ||
        isReplaceAllLyricsModalOpen ||
        isTimingOffsetModalOpen
    )
    setIsAnyModalOpen(modalOpen)
  }, [
    modalContent,
    editModalSegment,
    isReviewModalOpen,
    isAddLyricsModalOpen,
    isFindReplaceModalOpen,
    isReplaceAllLyricsModalOpen,
    isTimingOffsetModalOpen,
  ])

  // Effect to handle local mode navigation to instrumental review
  useEffect(() => {
    if (showInstrumentalReview && isLocalMode) {
      // In local mode, redirect to instrumental page served by ReviewServer
      window.location.href = `/app/jobs/local/instrumental`
    }
  }, [showInstrumentalReview, isLocalMode])

  // Countdown effect for success screen (auto-close/redirect after submission)
  useEffect(() => {
    if (!showSuccess) return

    const interval = setInterval(() => {
      setCountdown((prev) => {
        if (prev <= 1) {
          clearInterval(interval)
          if (isLocalMode) {
            // Note: window.close() may be blocked by browsers if the window wasn't opened
            // by script. The success screen shows "You can close this window now" as fallback.
            window.close()
          } else {
            router.push('/app')
          }
          return 0
        }
        return prev - 1
      })
    }, 1000)

    return () => clearInterval(interval)
  }, [showSuccess, isLocalMode, router])

  // Calculate effective mode based on modifier key states
  const effectiveMode = isCtrlPressed ? 'delete_word' : isShiftPressed ? 'highlight' : interactionMode

  // Edit feedback handler
  const handleEditFeedback = useCallback(
    (entryId: string, reason: EditFeedbackReason) => {
      attachFeedback(editLog, entryId, reason)
    },
    [editLog]
  )

  const handleAdvancedModeToggle = useCallback((enabled: boolean) => {
    setAdvancedMode(enabled)
    if (typeof window !== 'undefined') {
      localStorage.setItem('lyricsReviewAdvancedMode', String(enabled))
    }
  }, [])

  const showFeedbackForEntry = useCallback(
    (entry: EditLogEntry) => {
      // Only show for single-word edits
      if (!['word_change', 'word_delete', 'word_add'].includes(entry.operation)) return
      // Dismiss previous entry with no_response if still showing
      if (lastEditEntry && lastEditEntry.id !== entry.id) {
        attachFeedback(editLog, lastEditEntry.id, 'no_response')
      }
      setLastEditEntry(entry)
      setShowFeedbackBar(true)
    },
    [editLog, lastEditEntry]
  )

  // Show correction detail
  const handleShowCorrectionDetail = useCallback(
    (wordId: string) => {
      const correction = data.corrections?.find(
        (c) => c.corrected_word_id === wordId || c.word_id === wordId
      )
      if (!correction) return

      const categoryMatch = correction.reason?.match(/\[([A-Z_]+)\]/)
      const category = categoryMatch ? categoryMatch[1] : null
      const correctedWord =
        data.corrected_segments.flatMap((s) => s.words).find((w) => w.id === wordId)?.text ||
        correction.corrected_word

      setSelectedCorrection({
        wordId,
        originalWord: correction.original_word,
        correctedWord,
        category,
        confidence: correction.confidence,
        reason: correction.reason,
        handler: correction.handler,
        source: correction.source,
      })
      setCorrectionDetailOpen(true)
    },
    [data]
  )

  // Word click handler
  const handleWordClick = useCallback(
    (info: WordClickInfo) => {
      if (effectiveMode === 'delete_word') {
        const deletedWord = data.corrected_segments.flatMap((s) => s.words).find((w) => w.id === info.word_id)
        const segment = data.corrected_segments.find((s) => s.words.some((w) => w.id === info.word_id))
        const entry = addEditEntry(editLog, 'word_delete', {
          segment_id: segment?.id,
          segment_index: segment ? data.corrected_segments.indexOf(segment) : null,
          word_ids_before: [info.word_id],
          text_before: deletedWord?.text ?? '',
        })
        const newData = deleteWord(data, info.word_id)
        updateDataWithHistory(newData, 'delete word')
        handleFlash('word')
        showFeedbackForEntry(entry)
        return
      }

      if (effectiveMode === 'highlight') {
        const correction = data.corrections?.find(
          (c) => c.corrected_word_id === info.word_id || c.word_id === info.word_id
        )

        if (correction) {
          if (correction.handler === 'AgenticCorrector') {
            handleShowCorrectionDetail(info.word_id)
            return
          }

          setHighlightInfo({
            type: 'correction',
            transcribed_words: [],
            correction,
          })
          setFlashingType('word')
          return
        }

        // Check anchor sequences
        const anchor = data.anchor_sequences?.find(
          (a) =>
            a.transcribed_word_ids.includes(info.word_id) ||
            Object.values(a.reference_word_ids).some((ids) => ids.includes(info.word_id))
        )

        if (anchor) {
          const allWords = data.corrected_segments.flatMap((s) => s.words)
          const tempSegment: LyricsSegment = {
            id: 'temp',
            words: allWords,
            text: allWords.map((w) => w.text).join(' '),
            start_time: allWords[0]?.start_time ?? null,
            end_time: allWords[allWords.length - 1]?.end_time ?? null,
          }

          const transcribedWords = getWordsFromIds([tempSegment], anchor.transcribed_word_ids)
          const referenceWords = Object.fromEntries(
            Object.entries(anchor.reference_word_ids).map(([source, ids]) => {
              const sourceWords = data.reference_lyrics[source].segments.flatMap((s) => s.words)
              const tempSourceSegment: LyricsSegment = {
                id: `temp-${source}`,
                words: sourceWords,
                text: sourceWords.map((w) => w.text).join(' '),
                start_time: sourceWords[0]?.start_time ?? null,
                end_time: sourceWords[sourceWords.length - 1]?.end_time ?? null,
              }
              return [source, getWordsFromIds([tempSourceSegment], ids)]
            })
          )

          setHighlightInfo({
            type: 'anchor',
            sequence: anchor,
            transcribed_words: transcribedWords,
            reference_words: referenceWords,
          })
          setFlashingType('word')
          return
        }
      } else if (effectiveMode === 'edit') {
        const segmentIndex = data.corrected_segments.findIndex((segment) =>
          segment.words.some((word) => word.id === info.word_id)
        )

        if (segmentIndex !== -1) {
          const segment = data.corrected_segments[segmentIndex]
          setEditModalSegment({
            segment,
            index: segmentIndex,
            originalSegment: JSON.parse(JSON.stringify(segment)),
          })
        }
      }
    },
    [data, effectiveMode, updateDataWithHistory, handleFlash, handleShowCorrectionDetail]
  )

  // Update segment handler
  const handleUpdateSegment = useCallback(
    (updatedSegment: LyricsSegment) => {
      if (!editModalSegment) return

      const currentData = history[historyIndex]
      const newSegments = currentData.corrected_segments.map((segment, i) =>
        i === editModalSegment.index ? updatedSegment : segment
      )
      const newData: CorrectionData = {
        ...currentData,
        corrected_segments: newSegments,
      }

      // Diff original vs updated words to create edit log entries
      const originalSegment = editModalSegment.originalSegment || editModalSegment.segment
      const entries: EditLogEntry[] = []
      if (originalSegment && originalSegment.text !== updatedSegment.text) {
        const origWordMap = new Map(originalSegment.words.map((w) => [w.id, w]))
        const updatedWordMap = new Map(updatedSegment.words.map((w) => [w.id, w]))

        // Changed or deleted words
        for (const origWord of originalSegment.words) {
          const updatedWord = updatedWordMap.get(origWord.id)
          if (!updatedWord) {
            entries.push(addEditEntry(editLog, 'word_delete', {
              segment_id: originalSegment.id,
              segment_index: editModalSegment.index,
              word_ids_before: [origWord.id],
              text_before: origWord.text,
            }))
          } else if (updatedWord.text !== origWord.text) {
            entries.push(addEditEntry(editLog, 'word_change', {
              segment_id: originalSegment.id,
              segment_index: editModalSegment.index,
              word_ids_before: [origWord.id],
              word_ids_after: [updatedWord.id],
              text_before: origWord.text,
              text_after: updatedWord.text,
            }))
          }
        }

        // Added words
        for (const updatedWord of updatedSegment.words) {
          if (!origWordMap.has(updatedWord.id)) {
            entries.push(addEditEntry(editLog, 'word_add', {
              segment_id: originalSegment.id,
              segment_index: editModalSegment.index,
              word_ids_after: [updatedWord.id],
              text_after: updatedWord.text,
            }))
          }
        }

        // Show feedback bar only for single-word edits
        if (entries.length === 1) {
          showFeedbackForEntry(entries[0])
        }
      }

      updateDataWithHistory(newData, 'update segment')
      setEditModalSegment(null)
    },
    [history, historyIndex, editModalSegment, updateDataWithHistory, editLog, showFeedbackForEntry]
  )

  // Delete segment handler
  const handleDeleteSegment = useCallback(
    (segmentIndex: number) => {
      const segment = data.corrected_segments[segmentIndex]
      if (segment) {
        addEditEntry(editLog, 'segment_delete', {
          segment_id: segment.id,
          segment_index: segmentIndex,
          word_ids_before: segment.words.map((w) => w.id),
          text_before: segment.text,
        })
      }
      const newData = deleteSegment(data, segmentIndex)
      updateDataWithHistory(newData, 'delete segment')
    },
    [data, updateDataWithHistory, editLog]
  )

  // Correction action handlers
  const handleRevertCorrection = useCallback(
    (wordId: string) => {
      const correction = data.corrections?.find(
        (c) => c.corrected_word_id === wordId || c.word_id === wordId
      )
      if (!correction) return

      const segmentIndex = data.corrected_segments.findIndex((segment) =>
        segment.words.some((w) => w.id === wordId)
      )
      if (segmentIndex === -1) return

      const segment = data.corrected_segments[segmentIndex]
      const newWords = segment.words.map((word) => {
        if (word.id === wordId) {
          return {
            ...word,
            text: correction.original_word,
            id: correction.word_id,
          }
        }
        return word
      })

      const newText = newWords.map((w) => w.text).join(' ')
      const newSegment = { ...segment, words: newWords, text: newText }
      const newSegments = data.corrected_segments.map((seg, idx) =>
        idx === segmentIndex ? newSegment : seg
      )
      const newCorrections =
        data.corrections?.filter((c) => c.corrected_word_id !== wordId && c.word_id !== wordId) ||
        []

      const newData: CorrectionData = {
        ...data,
        corrected_segments: newSegments,
        corrections: newCorrections,
      }

      addEditEntry(editLog, 'revert_correction', {
        segment_id: segment.id,
        segment_index: segmentIndex,
        word_ids_before: [wordId],
        word_ids_after: [correction.word_id],
        text_before: correction.corrected_word,
        text_after: correction.original_word,
      })

      updateDataWithHistory(newData, 'revert correction')
    },
    [data, updateDataWithHistory, editLog]
  )

  const handleEditCorrection = useCallback(
    (wordId: string) => {
      const segmentIndex = data.corrected_segments.findIndex((segment) =>
        segment.words.some((w) => w.id === wordId)
      )
      if (segmentIndex === -1) return

      const segment = data.corrected_segments[segmentIndex]
      setEditModalSegment({
        segment,
        index: segmentIndex,
        originalSegment: segment,
      })
    },
    [data]
  )

  const handleAcceptCorrection = useCallback((wordId: string) => {
    // For now, just log acceptance
  }, [])

  // Batch action handlers
  const handleAcceptAllCorrections = useCallback(() => {
    data.corrections?.forEach((c) => {
      console.log('Batch accepted correction:', c.corrected_word_id || c.word_id)
    })
  }, [data.corrections])

  const handleAcceptHighConfidenceCorrections = useCallback(() => {
    const highConfidence = data.corrections?.filter((c) => c.confidence >= 0.8) || []
    highConfidence.forEach((c) => {
      console.log('Batch accepted high-confidence correction:', c.corrected_word_id || c.word_id)
    })
  }, [data.corrections])

  const handleRevertAllCorrections = useCallback(() => {
    if (
      typeof window === 'undefined' ||
      !window.confirm(
        `Are you sure you want to revert all ${data.corrections?.length || 0} corrections?`
      )
    ) {
      return
    }

    addEditEntry(editLog, 'revert_all', {
      details: { count: data.corrections?.length ?? 0 },
    })

    const corrections = [...(data.corrections || [])].reverse()
    let newData = data

    for (const correction of corrections) {
      const wordId = correction.corrected_word_id || correction.word_id
      const segmentIndex = newData.corrected_segments.findIndex((segment) =>
        segment.words.some((w) => w.id === wordId)
      )
      if (segmentIndex === -1) continue

      const segment = newData.corrected_segments[segmentIndex]
      const newWords = segment.words.map((word) => {
        if (word.id === wordId) {
          return { ...word, text: correction.original_word, id: correction.word_id }
        }
        return word
      })

      const newText = newWords.map((w) => w.text).join(' ')
      const newSegment = { ...segment, words: newWords, text: newText }
      const newSegments = newData.corrected_segments.map((seg, idx) =>
        idx === segmentIndex ? newSegment : seg
      )

      newData = {
        ...newData,
        corrected_segments: newSegments,
        corrections:
          newData.corrections?.filter(
            (c) => c.corrected_word_id !== wordId && c.word_id !== wordId
          ) || [],
      }
    }

    updateDataWithHistory(newData, 'revert all corrections')
  }, [data, updateDataWithHistory, editLog])

  // Finish review - opens preview modal
  const handleFinishReview = useCallback(() => {
    // Pause audio if playing to avoid clash with preview video
    if (typeof window !== 'undefined' && window.isAudioPlaying && window.toggleAudioPlayback) {
      window.toggleAudioPlayback()
    }
    // Trigger server-side session save on preview (signals completion milestone)
    saveSession('preview')
    setIsReviewModalOpen(true)
  }, [saveSession])

  // Submit to server (save corrections, don't complete review yet)
  const handleSubmitToServer = useCallback(async () => {
    if (!apiClient) return

    setIsSubmitting(true)
    try {
      const dataToSubmit =
        timingOffsetMs !== 0 ? applyOffsetToCorrectionData(data, timingOffsetMs) : data

      // 1. Save corrections (not final submission yet), including current duet flag
      await apiClient.submitCorrections(dataToSubmit, isDuet)

      // 2. Save edit log (non-blocking)
      if (editLog.entries.length > 0) {
        try {
          await apiClient.submitEditLog(editLog)
        } catch (error) {
          console.error('Failed to submit edit log:', error)
        }
      }

      // 3. Close modal
      setIsReviewModalOpen(false)
      setIsSubmitting(false)

      // 4. Navigate to instrumental review (or skip if existing instrumental uploaded)
      if (hasExistingInstrumental && jobId) {
        // Job has an instrumental uploaded at creation time — skip instrumental review
        // Submit with "custom" selection (maps to the uploaded instrumental)
        toast.success('Lyrics saved! Using uploaded instrumental, generating video...')
        try {
          const correctionData = await lyricsReviewApi.getCorrectionData(jobId)
          await lyricsReviewApi.completeReview(jobId, correctionData, 'custom', isDuet)
          setShowSuccess(true)
          setCountdown(3)
        } catch (err) {
          console.error('Failed to complete review with uploaded instrumental:', err)
          toast.error('Failed to start video generation. Please try again.')
        }
      } else if (isLocalMode) {
        // Local mode: Show transition message, navigation handled by browser/server
        toast.success('Lyrics saved! Loading instrumental review...')
        setShowInstrumentalReview(true)
      } else {
        // Cloud mode: Use hash-based navigation with jobId
        if (!jobId) {
          console.error('No jobId available for cloud mode navigation')
          toast.error('Navigation error - please return to dashboard')
          return
        }
        toast.success('Lyrics saved! Proceeding to instrumental review...')
        window.location.hash = `/${jobId}/instrumental`
      }
    } catch (error) {
      console.error('Failed to submit corrections:', error)
      toast.error('Failed to submit corrections. Please try again.')
      setIsSubmitting(false) // Reset on error so user can retry
    }
  }, [apiClient, data, timingOffsetMs, editLog, isLocalMode, jobId, hasExistingInstrumental])

  // Play segment handler
  const handlePlaySegment = useCallback(
    (startTime: number) => {
      if (typeof window !== 'undefined' && window.seekAndPlayAudio) {
        const adjustedStartTime =
          timingOffsetMs !== 0 ? startTime + timingOffsetMs / 1000 : startTime
        window.seekAndPlayAudio(adjustedStartTime)
      }
    },
    [timingOffsetMs]
  )

  // Reset corrections
  const handleResetCorrections = useCallback(() => {
    if (
      typeof window !== 'undefined' &&
      window.confirm('Are you sure you want to reset all corrections?')
    ) {
      clearSavedData(initialData)
      setHistory([JSON.parse(JSON.stringify(initialData))])
      setHistoryIndex(0)
      setModalContent(null)
      setFlashingType(null)
      setHighlightInfo(null)
      setInteractionMode('edit')
    }
  }, [initialData])

  // Segment operations
  const handleAddSegment = useCallback(
    (beforeIndex: number) => {
      const newData = addSegmentBefore(data, beforeIndex)
      updateDataWithHistory(newData, 'add segment')
    },
    [data, updateDataWithHistory]
  )

  const handleSplitSegment = useCallback(
    (segmentIndex: number, afterWordIndex: number) => {
      const newData = splitSegment(data, segmentIndex, afterWordIndex)
      if (newData) {
        updateDataWithHistory(newData, 'split segment')
        setEditModalSegment(null)
      }
    },
    [data, updateDataWithHistory]
  )

  const handleMergeSegment = useCallback(
    (segmentIndex: number, mergeWithNext: boolean) => {
      const newData = mergeSegment(data, segmentIndex, mergeWithNext)
      updateDataWithHistory(newData, 'merge segment')
      setEditModalSegment(null)
    },
    [data, updateDataWithHistory]
  )

  // Handler toggle
  const handleHandlerToggle = useCallback(
    async (handler: string, enabled: boolean) => {
      if (!apiClient) return

      try {
        setIsUpdatingHandlers(true)
        const currentEnabled = new Set(data.metadata.enabled_handlers || [])

        if (enabled) {
          currentEnabled.add(handler)
        } else {
          currentEnabled.delete(handler)
        }

        const newData = await apiClient.updateHandlers(Array.from(currentEnabled))
        updateDataWithHistory(newData, `toggle handler ${handler}`)

        setModalContent(null)
        setFlashingType(null)
        setHighlightInfo(null)
        handleFlash('corrected')
      } catch (error) {
        console.error('Failed to update handlers:', error)
        alert('Failed to update correction handlers. Please try again.')
      } finally {
        setIsUpdatingHandlers(false)
      }
    },
    [apiClient, data.metadata.enabled_handlers, handleFlash, updateDataWithHistory]
  )

  const handleHandlerClick = useCallback((handler: string) => {
    setFlashingHandler(handler)
    setFlashingType('handler')
    setTimeout(() => {
      setFlashingHandler(null)
      setFlashingType(null)
    }, 1500)
  }, [])

  // Modal spacebar handler
  const handleSetModalSpacebarHandler = useCallback(
    (handler: ((e: KeyboardEvent) => void) | undefined) => {
      setModalHandler(handler, !!handler)
    },
    []
  )

  // Add lyrics
  const handleAddLyrics = useCallback(
    async (source: string, lyrics: string) => {
      if (!apiClient) return

      try {
        setIsAddingLyrics(true)
        const newData = await apiClient.addLyrics(source, lyrics)
        updateDataWithHistory(newData, 'add lyrics')
      } finally {
        setIsAddingLyrics(false)
      }
    },
    [apiClient, updateDataWithHistory]
  )

  // Search lyrics (reference lyrics search)
  const handleSearchLyrics = useCallback(
    async (artist: string, title: string, forceSources: string[]): Promise<SearchLyricsResponse> => {
      if (!apiClient || !apiClient.searchLyrics) {
        return { status: 'no_results', message: 'No API client', sources_added: [], sources_rejected: {}, sources_not_found: [] }
      }
      const result = await apiClient.searchLyrics(artist, title, forceSources)
      if (result.status === 'success' && result.data) {
        updateDataWithHistory(result.data, 'search lyrics')
      }
      return result
    },
    [apiClient, updateDataWithHistory],
  )

  // Find/replace
  const handleFindReplace = useCallback(
    (
      findText: string,
      replaceText: string,
      options: { caseSensitive: boolean; useRegex: boolean; fullTextMode: boolean }
    ) => {
      const newData = findAndReplace(data, findText, replaceText, options)
      // Count how many segments changed
      const changedSegments = newData.corrected_segments.filter(
        (seg, i) => seg.text !== data.corrected_segments[i]?.text
      )
      addEditEntry(editLog, 'find_replace', {
        text_before: findText,
        text_after: replaceText,
        details: {
          pattern: findText,
          replacement: replaceText,
          segments_affected: changedSegments.length,
          ...options,
        },
      })
      updateDataWithHistory(newData, 'find/replace')
    },
    [data, updateDataWithHistory, editLog]
  )

  // Un-correct all
  const handleUnCorrectAll = useCallback(() => {
    if (!originalData.original_segments) return

    if (
      typeof window !== 'undefined' &&
      window.confirm('Are you sure you want to revert all segments to their original state?')
    ) {
      const newData: CorrectionData = {
        ...data,
        corrected_segments: JSON.parse(JSON.stringify(originalData.original_segments)),
      }
      updateDataWithHistory(newData, 'un-correct all segments')
    }
  }, [originalData.original_segments, data, updateDataWithHistory])

  // Replace all lyrics
  const handleReplaceAllLyrics = useCallback(() => {
    setIsReplaceAllLyricsModalOpen(true)
  }, [])

  const handleSaveReplaceAllLyrics = useCallback(
    (
      newSegments: LyricsSegment[],
      meta?: { operation: 'replace_all_lyrics' | 'change_case'; details?: Record<string, unknown> }
    ) => {
      const operation = meta?.operation ?? 'replace_all_lyrics'
      const details =
        meta?.details ?? {
          segments_before: data.corrected_segments.length,
          segments_after: newSegments.length,
        }
      addEditEntry(editLog, operation, { details })
      const newData = { ...data, corrected_segments: newSegments }
      const historyLabel = operation === 'change_case' ? 'change case' : 'replace all lyrics'
      updateDataWithHistory(newData, historyLabel)
      setIsReplaceAllLyricsModalOpen(false)
    },
    [data, updateDataWithHistory, editLog]
  )

  // Undo/Redo
  const handleUndo = useCallback(() => {
    if (historyIndex > 0) {
      setHistoryIndex(historyIndex - 1)
    }
  }, [historyIndex])

  const handleRedo = useCallback(() => {
    if (historyIndex < history.length - 1) {
      setHistoryIndex(historyIndex + 1)
    }
  }, [historyIndex, history])

  const canUndo = historyIndex > 0
  const canRedo = historyIndex < history.length - 1

  // Memoize metric click handlers
  const metricClickHandlers = useMemo(
    () => ({
      anchor: () => handleFlash('anchor'),
      corrected: () => handleFlash('corrected'),
      uncorrected: () => handleFlash('uncorrected'),
    }),
    [handleFlash]
  )

  // Display data with timing offset applied
  const displayData = useMemo(() => {
    return timingOffsetMs !== 0 ? applyOffsetToCorrectionData(data, timingOffsetMs) : data
  }, [data, timingOffsetMs])

  // Timing offset handlers
  const handleOpenTimingOffsetModal = useCallback(() => {
    setIsTimingOffsetModalOpen(true)
  }, [])

  const handleApplyTimingOffset = useCallback((offsetMs: number) => {
    setTimingOffsetMs(offsetMs)
  }, [])

  // Success screen - shown after review submission
  if (showSuccess) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-background">
        <div className="text-center max-w-md p-6">
          <div className="mb-4">
            <Check className="w-12 h-12 mx-auto text-green-500" />
          </div>
          <h2 className="text-2xl font-semibold text-green-500 mb-4">
            Review Submitted
          </h2>
          <p className="text-lg mb-2">
            Your lyrics corrections have been saved.
          </p>
          <p className="text-muted-foreground mb-4">
            Video generation has started and you&apos;ll receive a notification when it&apos;s ready.
          </p>
          <p className="text-muted-foreground">
            {countdown > 0
              ? `${isLocalMode ? 'Closing' : 'Redirecting'} in ${countdown}s...`
              : isLocalMode
                ? 'You can close this window now.'
                : 'Redirecting...'}
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="max-w-full overflow-x-hidden">
      <Header
        isReadOnly={isReadOnly}
        onFileLoad={onFileLoad}
        data={data}
        onMetricClick={metricClickHandlers}
        effectiveMode={effectiveMode}
        onModeChange={setInteractionMode}
        apiClient={apiClient}
        audioHash={audioHash}
        onTimeUpdate={setCurrentAudioTime}
        onHandlerToggle={handleHandlerToggle}
        isUpdatingHandlers={isUpdatingHandlers}
        onHandlerClick={handleHandlerClick}
        onFindReplace={() => setIsFindReplaceModalOpen(true)}
        onEditAll={handleReplaceAllLyrics}
        onTimingOffset={handleOpenTimingOffsetModal}
        timingOffsetMs={timingOffsetMs}
        onUndo={handleUndo}
        onRedo={handleRedo}
        canUndo={canUndo}
        canRedo={canRedo}
        onResetCorrections={handleResetCorrections}
        statsVisible={statsVisible}
        onStatsToggle={() => setStatsVisible((v) => !v)}
        reviewMode={reviewMode}
        onReviewModeToggle={setReviewMode}
        onAcceptAllCorrections={handleAcceptAllCorrections}
        onAcceptHighConfidenceCorrections={handleAcceptHighConfidenceCorrections}
        onRevertAllCorrections={handleRevertAllCorrections}
        isDuet={isDuet}
        onToggleDuet={() => setIsDuet(d => !d)}
      />

      <div className={cn('grid gap-2', isMobile ? 'grid-cols-1' : 'grid-cols-2')}>
        <TranscriptionView
          data={displayData}
          mode={effectiveMode}
          onElementClick={setModalContent}
          onWordClick={handleWordClick}
          flashingType={flashingType}
          flashingHandler={flashingHandler}
          highlightInfo={highlightInfo}
          onPlaySegment={handlePlaySegment}
          currentTime={isAnyModalOpen ? undefined : currentAudioTime}
          anchors={data.anchor_sequences}
          onDataChange={(updatedData) => {
            updateDataWithHistory(updatedData, 'direct data change')
          }}
          reviewMode={reviewMode}
          onRevertCorrection={handleRevertCorrection}
          onEditCorrection={handleEditCorrection}
          onAcceptCorrection={handleAcceptCorrection}
          onShowCorrectionDetail={handleShowCorrectionDetail}
          activeGapWordIds={activeGapWordIds}
          advancedMode={advancedMode}
          onAdvancedModeToggle={handleAdvancedModeToggle}
          editedWordIds={editedWordIds}
          isDuet={isDuet}
          onSegmentSingerChange={(segmentIdx, next) => {
            const segments = [...data.corrected_segments]
            segments[segmentIdx] = { ...segments[segmentIdx], singer: next }
            updateDataWithHistory({ ...data, corrected_segments: segments }, 'singer change')
          }}
          onSegmentFocus={setFocusedSegmentIndex}
        />
        <ReferenceView
          referenceSources={data.reference_lyrics}
          anchors={data.anchor_sequences}
          gaps={data.gap_sequences}
          mode={effectiveMode}
          onElementClick={setModalContent}
          onWordClick={handleWordClick}
          flashingType={flashingType}
          highlightInfo={highlightInfo}
          currentSource={currentSource}
          onSourceChange={setCurrentSource}
          corrected_segments={data.corrected_segments}
          corrections={data.corrections}
          onAddLyrics={() => setIsAddLyricsModalOpen(true)}
          onAddLyricsInline={handleAddLyrics}
          onSearchLyrics={handleSearchLyrics}
          defaultArtist={data.metadata?.artist || ''}
          defaultTitle={data.metadata?.title || ''}
        />
      </div>

      {/* Spacer for sticky footer */}
      {!isReadOnly && apiClient && <div className="h-16" />}

      {/* Sticky footer bar */}
      {!isReadOnly && apiClient && (
        <div className="fixed bottom-0 left-0 right-0 bg-background border-t shadow-lg py-3 px-4 z-50 flex justify-center items-center gap-4 flex-wrap">
          <GapNavigator
            currentGapIndex={currentGapIndex}
            totalGaps={uncorrectedGaps.length}
            onPrevGap={handlePrevGap}
            onNextGap={handleNextGap}
          />
          {data.gap_sequences.length > 0 && (
            <div className="flex items-center gap-1.5">
              <div className="w-16 h-1.5 bg-muted rounded-full overflow-hidden">
                <div
                  className="h-full bg-green-500 rounded-full transition-all"
                  style={{
                    width: `${data.gap_sequences.length > 0 ? ((data.gap_sequences.length - uncorrectedGaps.length) / data.gap_sequences.length) * 100 : 0}%`,
                  }}
                />
              </div>
              {uncorrectedGaps.length === 0 ? (
                <span className="text-xs text-green-500 flex items-center gap-0.5">
                  <CheckCircle2 className="h-3.5 w-3.5" />
                  All gaps reviewed
                </span>
              ) : (
                <span className="text-xs text-muted-foreground whitespace-nowrap">
                  {data.gap_sequences.length - uncorrectedGaps.length}/{data.gap_sequences.length} gaps reviewed
                </span>
              )}
            </div>
          )}
          {apiClient?.saveReviewSession && (
            <>
              <Button
                variant="outline"
                size="sm"
                onClick={() => saveSession('manual')}
                disabled={history.length <= 1}
              >
                Save Progress
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={handleOpenSessionBrowser}
              >
                Session History
              </Button>
            </>
          )}
          <span className="text-sm text-muted-foreground">Lyrics look good?</span>
          <Button onClick={handleFinishReview} disabled={isReviewComplete}>
            {isReviewComplete ? 'Review Complete' : 'Preview Video'}
            <Video className="ml-2 h-4 w-4" />
          </Button>
        </div>
      )}

      {/* Modals */}
      <EditModal
        open={Boolean(editModalSegment)}
        onClose={() => {
          setEditModalSegment(null)
          handleSetModalSpacebarHandler(undefined)
        }}
        segment={
          editModalSegment?.segment
            ? timingOffsetMs !== 0
              ? applyOffsetToSegment(editModalSegment.segment, timingOffsetMs)
              : editModalSegment.segment
            : null
        }
        segmentIndex={editModalSegment?.index ?? null}
        originalSegment={
          editModalSegment?.originalSegment
            ? timingOffsetMs !== 0
              ? applyOffsetToSegment(editModalSegment.originalSegment, timingOffsetMs)
              : editModalSegment.originalSegment
            : null
        }
        onSave={handleUpdateSegment}
        onDelete={handleDeleteSegment}
        onAddSegment={handleAddSegment}
        onSplitSegment={handleSplitSegment}
        onMergeSegment={handleMergeSegment}
        onPlaySegment={handlePlaySegment}
        currentTime={currentAudioTime}
        originalTranscribedSegment={
          editModalSegment?.segment &&
          editModalSegment?.index !== null &&
          originalData.original_segments
            ? (() => {
                const origSegment =
                  originalData.original_segments.find(
                    (s: LyricsSegment) => s.id === editModalSegment.segment.id
                  ) || null
                return origSegment && timingOffsetMs !== 0
                  ? applyOffsetToSegment(origSegment, timingOffsetMs)
                  : origSegment
              })()
            : null
        }
      />

      <ReviewChangesModal
        open={isReviewModalOpen}
        onClose={() => setIsReviewModalOpen(false)}
        data={data}
        onSubmit={handleSubmitToServer}
        isSubmitting={isSubmitting}
        apiClient={apiClient}
        timingOffsetMs={timingOffsetMs}
        isDuet={isDuet}
      />

      <AddLyricsModal
        open={isAddLyricsModalOpen}
        onClose={() => setIsAddLyricsModalOpen(false)}
        onAdd={handleAddLyrics}
        onSearch={handleSearchLyrics}
        defaultArtist={data.metadata?.artist || ''}
        defaultTitle={data.metadata?.title || ''}
      />

      <FindReplaceModal
        open={isFindReplaceModalOpen}
        onClose={() => setIsFindReplaceModalOpen(false)}
        onReplace={handleFindReplace}
        data={data}
      />

      <TimingOffsetModal
        open={isTimingOffsetModalOpen}
        onClose={() => setIsTimingOffsetModalOpen(false)}
        currentOffset={timingOffsetMs}
        onApply={handleApplyTimingOffset}
      />

      <ReplaceAllLyricsModal
        open={isReplaceAllLyricsModalOpen}
        onClose={() => setIsReplaceAllLyricsModalOpen(false)}
        onSave={handleSaveReplaceAllLyrics}
        onPlaySegment={handlePlaySegment}
        currentTime={currentAudioTime}
        setModalSpacebarHandler={handleSetModalSpacebarHandler}
        existingSegments={data.corrected_segments}
      />

      <EditFeedbackBar
        entry={lastEditEntry}
        visible={showFeedbackBar}
        onFeedback={handleEditFeedback}
        onDismiss={() => setShowFeedbackBar(false)}
      />

      {selectedCorrection && (
        <CorrectionDetailCard
          open={correctionDetailOpen}
          onClose={() => {
            setCorrectionDetailOpen(false)
            setSelectedCorrection(null)
          }}
          originalWord={selectedCorrection.originalWord}
          correctedWord={selectedCorrection.correctedWord}
          category={selectedCorrection.category}
          confidence={selectedCorrection.confidence}
          reason={selectedCorrection.reason}
          handler={selectedCorrection.handler}
          source={selectedCorrection.source}
          onRevert={() => {
            handleRevertCorrection(selectedCorrection.wordId)
            setCorrectionDetailOpen(false)
            setSelectedCorrection(null)
          }}
          onEdit={() => {
            handleEditCorrection(selectedCorrection.wordId)
            setCorrectionDetailOpen(false)
            setSelectedCorrection(null)
          }}
          onAccept={() => {
            handleAcceptCorrection(selectedCorrection.wordId)
            setCorrectionDetailOpen(false)
            setSelectedCorrection(null)
          }}
        />
      )}

      {apiClient?.saveReviewSession && (
        <SessionRestoreDialog
          open={sessionRestoreOpen}
          onClose={() => setSessionRestoreOpen(false)}
          onRestore={handleSessionRestore}
          sessions={savedSessions}
          apiClient={apiClient as LyricsReviewApiClient}
          isLoading={sessionsLoading}
        />
      )}
    </div>
  )
}
