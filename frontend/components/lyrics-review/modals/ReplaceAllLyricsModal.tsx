'use client'

import { useTranslations } from 'next-intl'
import { useState, useEffect, useCallback, useMemo } from 'react'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { X, ArrowLeft, ClipboardPaste, Sparkles, Info, Check, AlertTriangle } from 'lucide-react'
import { nanoid } from 'nanoid'
import { LyricsSegment, Word } from '@/lib/lyrics-review/types'
import { createWordsWithDistributedTiming } from '@/lib/lyrics-review/utils/wordUtils'
import ModeSelectionModal from './ModeSelectionModal'
import LyricsSynchronizer from '../synchronizer/LyricsSynchronizer'

declare global {
  interface Window {
    getAudioDuration?: () => number
    toggleAudioPlayback?: () => void
    isAudioPlaying?: boolean
  }
}

type ModalMode = 'selection' | 'replace' | 'resync' | 'replaceSegments'

interface ReplaceAllLyricsModalProps {
  open: boolean
  onClose: () => void
  onSave: (newSegments: LyricsSegment[]) => void
  onPlaySegment?: (startTime: number) => void
  currentTime?: number
  setModalSpacebarHandler: (handler: ((e: KeyboardEvent) => void) | undefined) => void
  existingSegments?: LyricsSegment[]
}

export default function ReplaceAllLyricsModal({
  open,
  onClose,
  onSave,
  onPlaySegment,
  currentTime = 0,
  setModalSpacebarHandler,
  existingSegments = [],
}: ReplaceAllLyricsModalProps) {
  const t = useTranslations('lyricsReview.modals.replaceAllLyrics')
  const [mode, setMode] = useState<ModalMode>('selection')
  const [inputText, setInputText] = useState('')
  const [newSegments, setNewSegments] = useState<LyricsSegment[]>([])

  // Reset state when modal opens
  useEffect(() => {
    if (open) {
      setMode('selection')
      setInputText('')
      setNewSegments([])
    }
  }, [open])

  // Parse the input text to get line and word counts
  const parseInfo = useMemo(() => {
    if (!inputText.trim()) return { lines: 0, words: 0 }

    const lines = inputText
      .trim()
      .split('\n')
      .filter((line) => line.trim().length > 0)
    const totalWords = lines.reduce((count, line) => {
      return count + line.trim().split(/\s+/).length
    }, 0)

    return { lines: lines.length, words: totalWords }
  }, [inputText])

  // Line count validation for replaceSegments mode
  const replaceSegmentsInfo = useMemo(() => {
    const expectedLineCount = existingSegments.length
    const lines = inputText.split('\n')
    const currentLineCount = lines.length
    const lineDiff = currentLineCount - expectedLineCount
    return { expectedLineCount, currentLineCount, lineDiff }
  }, [inputText, existingSegments])

  // Process the input text into segments and words
  const processLyrics = useCallback(() => {
    if (!inputText.trim()) return

    const lines = inputText
      .trim()
      .split('\n')
      .filter((line) => line.trim().length > 0)
    const segments: LyricsSegment[] = []

    lines.forEach((line) => {
      const words = line
        .trim()
        .split(/\s+/)
        .filter((word) => word.length > 0)
      const segmentWords: Word[] = words.map((wordText) => ({
        id: nanoid(),
        text: wordText,
        start_time: null,
        end_time: null,
        confidence: 1.0,
        created_during_correction: true,
      }))

      segments.push({
        id: nanoid(),
        text: line.trim(),
        words: segmentWords,
        start_time: null,
        end_time: null,
      })
    })

    setNewSegments(segments)
    setMode('resync')
  }, [inputText])

  // Handle paste from clipboard
  const handlePasteFromClipboard = useCallback(async () => {
    try {
      const text = await navigator.clipboard.readText()
      setInputText(text)
    } catch (error) {
      console.error('Failed to read from clipboard:', error)
      alert('Failed to read from clipboard. Please paste manually.')
    }
  }, [])

  // Handle modal close
  const handleClose = useCallback(() => {
    setMode('selection')
    setInputText('')
    setNewSegments([])
    onClose()
  }, [onClose])

  // Apply replace segments: build updated segments with new words for changed lines
  const handleApplyReplaceSegments = useCallback(() => {
    const newLines = inputText.split('\n')
    const updatedSegments: LyricsSegment[] = existingSegments.map((segment, i) => {
      const newLineText = newLines[i]?.trim() ?? ''
      const originalText = segment.text.trim()

      if (newLineText === originalText) {
        // Unchanged — deep copy to avoid mutation
        return JSON.parse(JSON.stringify(segment))
      }

      // Changed — create new words with distributed timing
      const newWords = createWordsWithDistributedTiming(
        newLineText,
        segment.start_time,
        segment.end_time
      )

      return {
        ...segment,
        text: newLineText,
        words: newWords,
      }
    })

    onSave(updatedSegments)
    handleClose()
  }, [inputText, existingSegments, onSave, handleClose])

  // Handle save from synchronizer
  const handleSave = useCallback(
    (segments: LyricsSegment[]) => {
      onSave(segments)
      handleClose()
    },
    [onSave, handleClose]
  )

  // Handle mode selection
  const handleSelectReplace = useCallback(() => {
    setMode('replace')
  }, [])

  const handleSelectResync = useCallback(() => {
    setMode('resync')
  }, [])

  const handleSelectReplaceSegments = useCallback(() => {
    setInputText(existingSegments.map((s) => s.text.trim()).join('\n'))
    setMode('replaceSegments')
  }, [existingSegments])

  // Handle back to selection
  const handleBackToSelection = useCallback(() => {
    setMode('selection')
    setInputText('')
    setNewSegments([])
  }, [])

  // Determine which segments to use for synchronizer
  const segmentsForSync = mode === 'resync' && newSegments.length > 0 ? newSegments : existingSegments

  // Check if we have existing lyrics
  const hasExistingLyrics =
    existingSegments.length > 0 && existingSegments.some((s) => s.words.length > 0)

  return (
    <>
      {/* Mode Selection Modal */}
      <ModeSelectionModal
        open={open && mode === 'selection'}
        onClose={handleClose}
        onSelectReplace={handleSelectReplace}
        onSelectResync={handleSelectResync}
        onSelectReplaceSegments={handleSelectReplaceSegments}
        hasExistingLyrics={hasExistingLyrics}
      />

      {/* Replace All Lyrics Modal (Paste Phase) */}
      <Dialog open={open && mode === 'replace'} onOpenChange={(isOpen) => !isOpen && handleClose()}>
        <DialogContent className="max-w-2xl h-[80vh] max-h-[80vh] flex flex-col">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Button variant="ghost" size="icon" onClick={handleBackToSelection} className="h-8 w-8">
                <ArrowLeft className="h-4 w-4" />
              </Button>
              <span className="flex-1">{t('replaceAll')}</span>
              <Button variant="ghost" size="icon" onClick={handleClose} className="h-8 w-8">
                <X className="h-4 w-4" />
              </Button>
            </DialogTitle>
          </DialogHeader>

          <div className="flex-1 overflow-hidden flex flex-col gap-4">
            <div>
              <h3 className="text-lg font-semibold">{t('pastePrompt')}</h3>
              <p className="text-sm text-muted-foreground mt-1">
                {t('eachLineNote')}
              </p>
            </div>

            <div className="flex items-center gap-4">
              <Button variant="outline" size="sm" onClick={handlePasteFromClipboard}>
                <ClipboardPaste className="h-4 w-4 mr-2" />
                {t('pasteFromClipboard')}
              </Button>
              <span className="text-sm text-muted-foreground font-medium">
                {t('lineWordCount', { lines: parseInfo.lines, words: parseInfo.words })}
              </span>
            </div>

            <Textarea
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              placeholder={t('placeholder')}
              className="flex-1 resize-none font-mono text-sm min-h-[300px]"
            />
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={handleClose}>
              Cancel
            </Button>
            <Button onClick={processLyrics} disabled={!inputText.trim()}>
              <Sparkles className="h-4 w-4 mr-2" />
              {t('continueToSync')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Replace Segment Lyrics Modal */}
      <Dialog open={open && mode === 'replaceSegments'} onOpenChange={(isOpen) => !isOpen && handleClose()}>
        <DialogContent className="max-w-2xl h-[80vh] max-h-[80vh] flex flex-col">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Button variant="ghost" size="icon" onClick={handleBackToSelection} className="h-8 w-8">
                <ArrowLeft className="h-4 w-4" />
              </Button>
              <span className="flex-1">{t('replaceSegment')}</span>
              <Button variant="ghost" size="icon" onClick={handleClose} className="h-8 w-8">
                <X className="h-4 w-4" />
              </Button>
            </DialogTitle>
          </DialogHeader>

          <div className="flex-1 overflow-hidden flex flex-col gap-4">
            <div className="flex items-start gap-2 p-3 rounded-md bg-muted/50 text-sm text-muted-foreground">
              <Info className="h-4 w-4 mt-0.5 shrink-0" />
              <p>
                {t('editLineByLine', { count: replaceSegmentsInfo.expectedLineCount })}
              </p>
            </div>

            <div className="flex items-center gap-4">
              <Button variant="outline" size="sm" onClick={handlePasteFromClipboard}>
                <ClipboardPaste className="h-4 w-4 mr-2" />
                {t('pasteFromClipboard')}
              </Button>
              <span className={`text-sm font-medium ${replaceSegmentsInfo.lineDiff === 0 ? 'text-green-500' : 'text-destructive'}`}>
                {replaceSegmentsInfo.lineDiff === 0 ? (
                  <span className="flex items-center gap-1">
                    <Check className="h-4 w-4" />
                    {replaceSegmentsInfo.currentLineCount}/{replaceSegmentsInfo.expectedLineCount} lines
                  </span>
                ) : (
                  <span className="flex items-center gap-1">
                    <AlertTriangle className="h-4 w-4" />
                    {replaceSegmentsInfo.currentLineCount}/{replaceSegmentsInfo.expectedLineCount} lines
                  </span>
                )}
              </span>
            </div>

            <Textarea
              value={inputText}
              onChange={(e) => setInputText(e.target.value)}
              placeholder="Edit lyrics line by line..."
              className="flex-1 resize-none font-mono text-sm min-h-[300px]"
            />

            {replaceSegmentsInfo.lineDiff !== 0 && (
              <div className="flex items-start gap-2 p-3 rounded-md bg-destructive/10 text-sm text-destructive">
                <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
                <p>
                  {replaceSegmentsInfo.lineDiff > 0
                    ? t('tooManyLines', { diff: Math.abs(replaceSegmentsInfo.lineDiff), expected: replaceSegmentsInfo.expectedLineCount, actual: replaceSegmentsInfo.currentLineCount })
                    : t('tooFewLines', { diff: Math.abs(replaceSegmentsInfo.lineDiff), expected: replaceSegmentsInfo.expectedLineCount, actual: replaceSegmentsInfo.currentLineCount })}
                </p>
              </div>
            )}
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={handleClose}>
              Cancel
            </Button>
            <Button
              onClick={handleApplyReplaceSegments}
              disabled={replaceSegmentsInfo.lineDiff !== 0}
            >
              Apply
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Synchronizer Modal */}
      <Dialog open={open && mode === 'resync'} onOpenChange={(isOpen) => !isOpen && handleClose()}>
        <DialogContent className="max-w-[calc(100vw-4vw)] w-[calc(100vw-4vw)] h-[90vh] max-h-[90vh] flex flex-col">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Button variant="ghost" size="icon" onClick={handleBackToSelection} className="h-8 w-8">
                <ArrowLeft className="h-4 w-4" />
              </Button>
              <span className="flex-1">
                {newSegments.length > 0 ? t('syncNewLyrics') : t('resyncExisting')}
              </span>
              <Button variant="ghost" size="icon" onClick={handleClose} className="h-8 w-8">
                <X className="h-4 w-4" />
              </Button>
            </DialogTitle>
          </DialogHeader>

          <div className="flex-1 overflow-hidden">
            {segmentsForSync.length > 0 ? (
              <LyricsSynchronizer
                segments={segmentsForSync}
                currentTime={currentTime}
                onPlaySegment={onPlaySegment}
                onSave={handleSave}
                onCancel={handleClose}
                setModalSpacebarHandler={setModalSpacebarHandler}
              />
            ) : (
              <div className="flex flex-col items-center justify-center h-full gap-4">
                <h3 className="text-lg font-semibold text-muted-foreground">{t('noLyricsToSync')}</h3>
                <p className="text-sm text-muted-foreground">
                  {t('goBackAndPaste')}
                </p>
                <Button variant="outline" onClick={handleBackToSelection}>
                  <ArrowLeft className="h-4 w-4 mr-2" />
                  {t('backToSelection')}
                </Button>
              </div>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}
