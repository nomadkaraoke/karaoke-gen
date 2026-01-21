'use client'

import { useState, useEffect, useCallback, useRef } from 'react'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Loader2, Play, Square, RotateCcw, Trash2, History } from 'lucide-react'
import { LyricsSegment, Word } from '@/lib/lyrics-review/types'
import { nanoid } from 'nanoid'
import EditWordList from '../EditWordList'
import EditTimelineSection from '../EditTimelineSection'
import useManualSync from '@/hooks/useManualSync'
import { setModalHandler } from '@/lib/lyrics-review/utils/keyboardHandlers'

interface EditModalProps {
  open: boolean
  onClose: () => void
  segment: LyricsSegment | null
  segmentIndex: number | null
  originalSegment: LyricsSegment | null
  onSave: (updatedSegment: LyricsSegment) => void
  onPlaySegment?: (startTime: number) => void
  currentTime?: number
  onDelete?: (segmentIndex: number) => void
  onAddSegment?: (segmentIndex: number) => void
  onSplitSegment?: (segmentIndex: number, afterWordIndex: number) => void
  onMergeSegment?: (segmentIndex: number, mergeWithNext: boolean) => void
  setModalSpacebarHandler?: (handler: ((e: KeyboardEvent) => void) | undefined) => void
  originalTranscribedSegment?: LyricsSegment | null
  isGlobal?: boolean
  isLoading?: boolean
}

export default function EditModal({
  open,
  onClose,
  segment,
  segmentIndex,
  originalSegment,
  onSave,
  onPlaySegment,
  currentTime = 0,
  onDelete,
  onAddSegment,
  onSplitSegment,
  onMergeSegment,
  setModalSpacebarHandler,
  originalTranscribedSegment,
  isGlobal = false,
  isLoading = false,
}: EditModalProps) {
  const [editedSegment, setEditedSegment] = useState<LyricsSegment | null>(segment)
  const [isPlaying, setIsPlaying] = useState(false)
  const currentTimeRef = useRef(currentTime)

  // Keep currentTimeRef updated
  useEffect(() => {
    currentTimeRef.current = currentTime
  }, [currentTime])

  useEffect(() => {
    setEditedSegment(segment)
  }, [segment])

  // Track playing state from global window
  useEffect(() => {
    const checkPlayingState = () => {
      setIsPlaying(!!window.isAudioPlaying)
    }
    // Check immediately and set up interval
    checkPlayingState()
    const intervalId = setInterval(checkPlayingState, 100)
    return () => clearInterval(intervalId)
  }, [])

  const updateSegment = useCallback((newWords: Word[]) => {
    if (!editedSegment) return

    const validStartTimes = newWords.map((w) => w.start_time).filter((t): t is number => t !== null)
    const validEndTimes = newWords.map((w) => w.end_time).filter((t): t is number => t !== null)

    const segmentStartTime = validStartTimes.length > 0 ? Math.min(...validStartTimes) : null
    const segmentEndTime = validEndTimes.length > 0 ? Math.max(...validEndTimes) : null

    setEditedSegment({
      ...editedSegment,
      words: newWords,
      text: newWords.map((w) => w.text).join(' '),
      start_time: segmentStartTime,
      end_time: segmentEndTime,
    })
  }, [editedSegment])

  // Manual sync hook
  const {
    isManualSyncing,
    syncWordIndex,
    startManualSync,
    handleSpacebar,
    isSpacebarPressed,
    handleTapStart,
    handleTapEnd,
    cleanupManualSync,
  } = useManualSync({
    editedSegment,
    currentTime,
    onPlaySegment,
    updateSegment,
  })

  // Wire up spacebar handler - use setModalHandler directly for proper modal state
  useEffect(() => {
    if (open) {
      setModalHandler(handleSpacebar, true)
    }
    return () => {
      setModalHandler(undefined, false)
    }
  }, [open, handleSpacebar])

  // Auto-stop playback at segment end (not during manual sync or for global segments)
  useEffect(() => {
    if (!editedSegment || isManualSyncing || isGlobal) return

    const endTime = editedSegment.end_time ?? 0
    if (isPlaying && currentTime > endTime) {
      window.toggleAudioPlayback?.()
    }
  }, [currentTime, editedSegment, isManualSyncing, isGlobal, isPlaying])

  const handleWordChange = useCallback(
    (index: number, updates: Partial<Word>) => {
      if (!editedSegment) return
      const newWords = [...editedSegment.words]
      newWords[index] = { ...newWords[index], ...updates }
      updateSegment(newWords)
    },
    [editedSegment, updateSegment]
  )

  const handleAddWord = useCallback(
    (index?: number) => {
      if (!editedSegment) return
      const newWords = [...editedSegment.words]
      const segStart = editedSegment.start_time ?? 0
      const segEnd = editedSegment.end_time ?? segStart + 1

      // Calculate timing for the new word based on insertion position
      let newStartTime: number
      let newEndTime: number

      if (index === undefined || index === -1) {
        // Inserting at beginning
        const firstWord = newWords[0]
        const firstWordStart = firstWord?.start_time ?? segEnd
        newStartTime = segStart
        newEndTime = segStart + (firstWordStart - segStart) / 2
        // If gap is too small, create a small default duration
        if (newEndTime - newStartTime < 0.1) {
          newEndTime = newStartTime + 0.2
        }
      } else {
        // Inserting after word at index
        const prevWord = newWords[index]
        const nextWord = newWords[index + 1]
        const prevEnd = prevWord?.end_time ?? segStart
        const nextStart = nextWord?.start_time ?? segEnd

        // Fill the gap between prev end and next start
        newStartTime = prevEnd
        newEndTime = nextStart
        // If gap is too small, create a small default duration
        if (newEndTime - newStartTime < 0.1) {
          newEndTime = newStartTime + 0.2
        }
      }

      const newWord: Word = {
        id: nanoid(),
        text: '',
        start_time: newStartTime,
        end_time: newEndTime,
        confidence: 1.0,
      }

      if (index === undefined || index === -1) {
        newWords.unshift(newWord)
      } else {
        newWords.splice(index + 1, 0, newWord)
      }
      updateSegment(newWords)
    },
    [editedSegment, updateSegment]
  )

  const handleSplitWord = useCallback(
    (index: number) => {
      if (!editedSegment) return
      const word = editedSegment.words[index]
      const words = word.text.split(/\s+/).filter((w) => w.length > 0)

      if (words.length <= 1) {
        const half = Math.ceil(word.text.length / 2)
        words[0] = word.text.slice(0, half)
        words[1] = word.text.slice(half)
      }

      const newWords = words.map((text) => ({
        id: nanoid(),
        text,
        start_time: null,
        end_time: null,
        confidence: 1.0,
      }))

      const allWords = [...editedSegment.words]
      allWords.splice(index, 1, ...newWords)
      updateSegment(allWords)
    },
    [editedSegment, updateSegment]
  )

  const handleMergeWords = useCallback(
    (index: number) => {
      if (!editedSegment || index >= editedSegment.words.length - 1) return

      const word1 = editedSegment.words[index]
      const word2 = editedSegment.words[index + 1]
      const newWords = [...editedSegment.words]

      newWords.splice(index, 2, {
        id: nanoid(),
        text: `${word1.text} ${word2.text}`.trim(),
        start_time: word1.start_time,
        end_time: word2.end_time,
        confidence: 1.0,
      })
      updateSegment(newWords)
    },
    [editedSegment, updateSegment]
  )

  const handleRemoveWord = useCallback(
    (index: number) => {
      if (!editedSegment) return
      const newWords = editedSegment.words.filter((_, i) => i !== index)
      updateSegment(newWords)
    },
    [editedSegment, updateSegment]
  )

  const handleReplaceAllWords = useCallback(
    (replacementText: string) => {
      if (!editedSegment) return
      const wordTexts = replacementText.trim().split(/\s+/).filter((w) => w.length > 0)
      if (wordTexts.length === 0) return

      const segStart = editedSegment.start_time ?? 0
      const segEnd = editedSegment.end_time ?? segStart + 1
      const totalDuration = segEnd - segStart
      const wordDuration = totalDuration / wordTexts.length

      const newWords: Word[] = wordTexts.map((text, index) => ({
        id: nanoid(),
        text,
        start_time: segStart + index * wordDuration,
        end_time: segStart + (index + 1) * wordDuration,
        confidence: 1.0,
      }))

      updateSegment(newWords)
    },
    [editedSegment, updateSegment]
  )

  const handleReset = useCallback(() => {
    if (!originalSegment) return
    setEditedSegment(JSON.parse(JSON.stringify(originalSegment)))
  }, [originalSegment])

  const handleRevertToOriginal = useCallback(() => {
    if (!originalTranscribedSegment) return
    setEditedSegment(JSON.parse(JSON.stringify(originalTranscribedSegment)))
  }, [originalTranscribedSegment])

  const handleClose = useCallback(() => {
    cleanupManualSync()
    onClose()
  }, [cleanupManualSync, onClose])

  const handleSave = useCallback(() => {
    if (!editedSegment) return
    onSave(editedSegment)
    handleClose()
  }, [editedSegment, onSave, handleClose])

  // Handle Enter key to save (same as clicking Save button)
  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    // Only handle Enter key
    if (e.key !== 'Enter') return
    // Don't save during manual sync mode
    if (isManualSyncing) return
    // Don't save if no valid segment
    if (!editedSegment || editedSegment.words.length === 0) return

    e.preventDefault()
    handleSave()
  }, [isManualSyncing, editedSegment, handleSave])

  const handlePlayToggle = useCallback(() => {
    if (!segment || !onPlaySegment) return
    if (isPlaying) {
      window.toggleAudioPlayback?.()
    } else {
      onPlaySegment(segment.start_time ?? 0)
    }
  }, [segment, onPlaySegment, isPlaying])

  const handleDelete = useCallback(() => {
    if (segmentIndex !== null) {
      onDelete?.(segmentIndex)
      handleClose()
    }
  }, [segmentIndex, onDelete, handleClose])

  const handleSplitSegment = useCallback(
    (wordIndex: number) => {
      if (segmentIndex !== null && editedSegment) {
        handleSave()
        onSplitSegment?.(segmentIndex, wordIndex)
      }
    },
    [segmentIndex, editedSegment, handleSave, onSplitSegment]
  )

  const handleMergeSegment = useCallback(
    (mergeWithNext: boolean) => {
      if (segmentIndex !== null && editedSegment) {
        handleSave()
        onMergeSegment?.(segmentIndex, mergeWithNext)
        handleClose()
      }
    },
    [segmentIndex, editedSegment, handleSave, onMergeSegment, handleClose]
  )

  const handleAddSegmentAt = useCallback(
    (beforeIndex: number) => {
      if (segmentIndex !== null && editedSegment) {
        handleSave()
        // beforeIndex 0 means before this segment, anything else means after
        const insertIndex = beforeIndex === 0 ? segmentIndex : segmentIndex + 1
        onAddSegment?.(insertIndex)
        handleClose()
      }
    },
    [segmentIndex, editedSegment, handleSave, onAddSegment, handleClose]
  )

  if (!isLoading && (!segment || !editedSegment || !originalSegment)) {
    return null
  }
  if (!isLoading && !isGlobal && segmentIndex === null) {
    return null
  }

  const startTime = editedSegment?.start_time ?? 0
  const endTime = editedSegment?.end_time ?? startTime + 1

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && handleClose()}>
      <DialogContent className="max-w-[960px] max-h-[90vh] overflow-hidden flex flex-col" onKeyDown={handleKeyDown}>
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            Edit {isGlobal ? 'All Words' : `Segment ${segmentIndex}`}
            {segment && segment.start_time !== null && onPlaySegment && (
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8"
                onClick={handlePlayToggle}
              >
                {isPlaying ? <Square className="h-4 w-4" /> : <Play className="h-4 w-4" />}
              </Button>
            )}
          </DialogTitle>
        </DialogHeader>

        {isLoading ? (
          <div className="flex flex-col items-center justify-center flex-1 py-12">
            <Loader2 className="h-12 w-12 animate-spin text-primary" />
            <p className="mt-4 text-lg font-semibold">
              Loading {isGlobal ? 'all words' : 'segment'}...
            </p>
          </div>
        ) : editedSegment ? (
          <div className="flex-1 overflow-auto space-y-4">
            {/* Timeline editor with Tap To Sync */}
            {editedSegment.words.some((w) => w.start_time !== null) && (
              <EditTimelineSection
                words={editedSegment.words}
                startTime={startTime}
                endTime={endTime}
                originalStartTime={originalSegment?.start_time ?? null}
                originalEndTime={originalSegment?.end_time ?? null}
                currentStartTime={editedSegment.start_time}
                currentEndTime={editedSegment.end_time}
                currentTime={currentTime}
                isManualSyncing={isManualSyncing}
                syncWordIndex={syncWordIndex}
                isSpacebarPressed={isSpacebarPressed}
                onWordUpdate={handleWordChange}
                onPlaySegment={onPlaySegment}
                startManualSync={startManualSync}
                isGlobal={isGlobal}
                onTapStart={handleTapStart}
                onTapEnd={handleTapEnd}
              />
            )}

            {/* Word list */}
            <EditWordList
              words={editedSegment.words}
              onWordUpdate={handleWordChange}
              onSplitWord={handleSplitWord}
              onMergeWords={handleMergeWords}
              onAddWord={handleAddWord}
              onRemoveWord={handleRemoveWord}
              onReplaceAllWords={handleReplaceAllWords}
              onSplitSegment={handleSplitSegment}
              onAddSegment={handleAddSegmentAt}
              onMergeSegment={handleMergeSegment}
              isGlobal={isGlobal}
            />
          </div>
        ) : (
          <div className="flex items-center justify-center flex-1">
            <p className="text-lg">No segment data available</p>
          </div>
        )}

        <DialogFooter className="flex flex-wrap gap-2 justify-between sm:justify-between">
          <div className="flex gap-2 flex-wrap">
            <Button
              variant="outline"
              size="sm"
              onClick={handleReset}
              className="text-orange-600 border-orange-600 hover:bg-orange-50 dark:text-orange-400 dark:border-orange-400 dark:hover:bg-orange-950"
            >
              <RotateCcw className="h-4 w-4 mr-1" />
              Reset
            </Button>
            {originalTranscribedSegment && (
              <Button variant="outline" size="sm" onClick={handleRevertToOriginal}>
                <History className="h-4 w-4 mr-1" />
                Un-Correct
              </Button>
            )}
            {!isGlobal && onDelete && (
              <Button variant="destructive" size="sm" onClick={handleDelete}>
                <Trash2 className="h-4 w-4 mr-1" />
                Delete Segment
              </Button>
            )}
          </div>
          <div className="flex gap-2 ml-auto">
            <Button variant="outline" onClick={handleClose}>
              Cancel
            </Button>
            <Button onClick={handleSave} disabled={isLoading || !editedSegment || editedSegment.words.length === 0}>
              Save
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
