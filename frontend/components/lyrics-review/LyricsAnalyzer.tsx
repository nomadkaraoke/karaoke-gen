'use client'

import { useState, useEffect, useCallback, useMemo, memo } from 'react'
import { Button } from '@/components/ui/button'
import { Video } from 'lucide-react'
import {
  AnchorSequence,
  CorrectionData,
  GapSequence,
  HighlightInfo,
  InteractionMode,
  LyricsSegment,
  ReferenceSource,
  WordCorrection,
  CorrectionAnnotation,
  FlashType,
  WordClickInfo,
  ModalContent,
} from '@/lib/lyrics-review/types'
import { cn } from '@/lib/utils'
import ReferenceView from './ReferenceView'
import TranscriptionView from './TranscriptionView'
import { EditModal } from './modals'
import { ReviewChangesModal } from './modals'
import { ReplaceAllLyricsModal } from './modals'
import { CorrectionAnnotationModal } from './modals'
import { AddLyricsModal } from './modals'
import { FindReplaceModal } from './modals'
import { TimingOffsetModal } from './modals'
import CorrectionDetailCard from './CorrectionDetailCard'
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
import Header from './Header'
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
  submitCorrections: (data: CorrectionData) => Promise<void>
  submitAnnotations: (annotations: Omit<CorrectionAnnotation, 'annotation_id' | 'timestamp'>[]) => Promise<void>
  updateHandlers: (handlers: string[]) => Promise<CorrectionData>
  addLyrics: (source: string, lyrics: string) => Promise<CorrectionData>
  getAudioUrl: (hash: string) => string
  generatePreviewVideo: (data: CorrectionData) => Promise<{
    status: string
    message?: string
    preview_hash?: string
  }>
  getPreviewVideoUrl: (hash: string) => string
}

export interface LyricsAnalyzerProps {
  data: CorrectionData
  onFileLoad: () => void
  onShowMetadata: () => void
  apiClient: ApiClient | null
  isReadOnly: boolean
  audioHash: string
}

export default function LyricsAnalyzer({
  data: initialData,
  onFileLoad,
  apiClient,
  isReadOnly,
  audioHash,
}: LyricsAnalyzerProps) {
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

  // Annotation state
  const [annotations, setAnnotations] = useState<Omit<CorrectionAnnotation, 'annotation_id' | 'timestamp'>[]>([])
  const [isAnnotationModalOpen, setIsAnnotationModalOpen] = useState(false)
  const [pendingAnnotation, setPendingAnnotation] = useState<{
    originalText: string
    correctedText: string
    wordIdsAffected: string[]
    gapId?: string
  } | null>(null)
  const [annotationsEnabled, setAnnotationsEnabled] = useState(() => {
    if (typeof window === 'undefined') return true
    const saved = localStorage.getItem('annotationsEnabled')
    return saved !== null ? saved === 'true' : true
  })

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

  // Reset history when initial data changes
  useEffect(() => {
    setHistory([initialData])
    setHistoryIndex(0)
  }, [initialData])

  // Load saved data
  useEffect(() => {
    const savedData = loadSavedData(initialData)
    if (savedData && typeof window !== 'undefined' && window.confirm('Found saved progress for this song. Would you like to restore it?')) {
      setHistory([savedData])
      setHistoryIndex(0)
    }
  }, [initialData])

  // Save data
  useEffect(() => {
    if (!isReadOnly) {
      saveData(data, initialData)
    }
  }, [data, isReadOnly, initialData])

  // Keyboard handlers
  useEffect(() => {
    const { handleKeyDown, handleKeyUp, cleanup } = setupKeyboardHandlers({
      setIsShiftPressed,
      setIsCtrlPressed,
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
  }, [isAnyModalOpen])

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

  // Calculate effective mode based on modifier key states
  const effectiveMode = isCtrlPressed ? 'delete_word' : isShiftPressed ? 'highlight' : interactionMode

  // Flash handler
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

  // Annotation handlers
  const handleSaveAnnotation = useCallback(
    (annotation: Omit<CorrectionAnnotation, 'annotation_id' | 'timestamp'>) => {
      setAnnotations((prev) => [...prev, annotation])
    },
    []
  )

  const handleSkipAnnotation = useCallback(() => {
    // No-op for now
  }, [])

  const handleAnnotationsToggle = useCallback((enabled: boolean) => {
    setAnnotationsEnabled(enabled)
    if (typeof window !== 'undefined') {
      localStorage.setItem('annotationsEnabled', String(enabled))
    }
  }, [])

  const triggerAnnotationModal = useCallback(
    (originalText: string, correctedText: string, wordIdsAffected: string[], gapId?: string) => {
      if (!annotationsEnabled || isReadOnly) return
      setPendingAnnotation({ originalText, correctedText, wordIdsAffected, gapId })
      setIsAnnotationModalOpen(true)
    },
    [annotationsEnabled, isReadOnly]
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
        const newData = deleteWord(data, info.word_id)
        updateDataWithHistory(newData, 'delete word')
        handleFlash('word')
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

      updateDataWithHistory(newData, 'update segment')

      const originalSegment = editModalSegment.originalSegment || editModalSegment.segment
      if (originalSegment && originalSegment.text !== updatedSegment.text) {
        const wordIds = updatedSegment.words.map((w) => w.id)
        triggerAnnotationModal(originalSegment.text, updatedSegment.text, wordIds)
      }

      setEditModalSegment(null)
    },
    [history, historyIndex, editModalSegment, updateDataWithHistory, triggerAnnotationModal]
  )

  // Delete segment handler
  const handleDeleteSegment = useCallback(
    (segmentIndex: number) => {
      const newData = deleteSegment(data, segmentIndex)
      updateDataWithHistory(newData, 'delete segment')
    },
    [data, updateDataWithHistory]
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

      updateDataWithHistory(newData, 'revert correction')
    },
    [data, updateDataWithHistory]
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
  }, [data, updateDataWithHistory])

  // Finish review
  const handleFinishReview = useCallback(() => {
    setIsReviewModalOpen(true)
  }, [])

  // Submit to server
  const handleSubmitToServer = useCallback(async () => {
    if (!apiClient) return

    try {
      const dataToSubmit =
        timingOffsetMs !== 0 ? applyOffsetToCorrectionData(data, timingOffsetMs) : data

      await apiClient.submitCorrections(dataToSubmit)

      if (annotations.length > 0) {
        try {
          await apiClient.submitAnnotations(annotations)
        } catch (error) {
          console.error('Failed to submit annotations:', error)
        }
      }

      setIsReviewComplete(true)
      setIsReviewModalOpen(false)
      window.close()
    } catch (error) {
      console.error('Failed to submit corrections:', error)
      alert('Failed to submit corrections. Please try again.')
    }
  }, [apiClient, data, timingOffsetMs, annotations])

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

  // Find/replace
  const handleFindReplace = useCallback(
    (
      findText: string,
      replaceText: string,
      options: { caseSensitive: boolean; useRegex: boolean; fullTextMode: boolean }
    ) => {
      const newData = findAndReplace(data, findText, replaceText, options)
      updateDataWithHistory(newData, 'find/replace')
    },
    [data, updateDataWithHistory]
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
    (newSegments: LyricsSegment[]) => {
      const newData = { ...data, corrected_segments: newSegments }
      updateDataWithHistory(newData, 'replace all lyrics')
      setIsReplaceAllLyricsModalOpen(false)
    },
    [data, updateDataWithHistory]
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
        onUnCorrectAll={handleUnCorrectAll}
        onResetCorrections={handleResetCorrections}
        annotationsEnabled={annotationsEnabled}
        onAnnotationsToggle={handleAnnotationsToggle}
        reviewMode={reviewMode}
        onReviewModeToggle={setReviewMode}
        onAcceptAllCorrections={handleAcceptAllCorrections}
        onAcceptHighConfidenceCorrections={handleAcceptHighConfidenceCorrections}
        onRevertAllCorrections={handleRevertAllCorrections}
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
        />
      </div>

      {/* Spacer for sticky footer */}
      {!isReadOnly && apiClient && <div className="h-16" />}

      {/* Sticky footer bar */}
      {!isReadOnly && apiClient && (
        <div className="fixed bottom-0 left-0 right-0 bg-background border-t shadow-lg py-3 px-4 z-50 flex justify-center items-center gap-4">
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
        apiClient={apiClient}
        timingOffsetMs={timingOffsetMs}
      />

      <AddLyricsModal
        open={isAddLyricsModalOpen}
        onClose={() => setIsAddLyricsModalOpen(false)}
        onAdd={handleAddLyrics}
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

      {pendingAnnotation && (
        <CorrectionAnnotationModal
          open={isAnnotationModalOpen}
          onClose={() => {
            setIsAnnotationModalOpen(false)
            setPendingAnnotation(null)
          }}
          onSave={handleSaveAnnotation}
          onSkip={handleSkipAnnotation}
          originalText={pendingAnnotation.originalText}
          correctedText={pendingAnnotation.correctedText}
          wordIdsAffected={pendingAnnotation.wordIdsAffected}
          referenceSources={Object.keys(data.reference_lyrics || {})}
          audioHash={audioHash}
          artist={data.metadata?.audio_filepath?.split('/').pop()?.split('.')[0] || 'Unknown'}
          title={data.metadata?.audio_filepath?.split('/').pop()?.split('.')[0] || 'Unknown'}
          sessionId={audioHash}
          gapId={pendingAnnotation.gapId}
        />
      )}

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
    </div>
  )
}
