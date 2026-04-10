'use client'

import React from 'react'
import { Button } from '@/components/ui/button'
import { Copy } from 'lucide-react'
import { WordComponent } from './Word'
import { useWordClick } from './useWordClick'
import {
  AnchorSequence,
  GapSequence,
  HighlightInfo,
  InteractionMode,
  LyricsSegment,
  Word,
  WordCorrection,
  ModalContent,
  FlashType,
  LinePosition,
  TranscriptionWordPosition,
  WordClickInfo,
} from '@/lib/lyrics-review/types'
import { getWordsFromIds } from '@/lib/lyrics-review/utils/wordUtils'
import CorrectedWordWithActions from '../CorrectedWordWithActions'

export interface HighlightedTextProps {
  text?: string
  segments?: LyricsSegment[]
  wordPositions: TranscriptionWordPosition[]
  anchors: AnchorSequence[]
  highlightInfo: HighlightInfo | null
  mode: InteractionMode
  onElementClick: (content: ModalContent) => void
  onWordClick?: (info: WordClickInfo) => void
  flashingType: FlashType
  isReference?: boolean
  currentSource?: string
  preserveSegments?: boolean
  linePositions?: LinePosition[]
  currentTime?: number
  referenceCorrections?: Map<string, string>
  gaps?: GapSequence[]
  flashingHandler?: string | null
  corrections?: WordCorrection[]
  // Gap navigation
  activeGapWordIds?: Set<string>
  // Review mode props for agentic corrections
  reviewMode?: boolean
  onRevertCorrection?: (wordId: string) => void
  onEditCorrection?: (wordId: string) => void
  onAcceptCorrection?: (wordId: string) => void
  onShowCorrectionDetail?: (wordId: string) => void
  // User-edited word tracking
  editedWordIds?: Set<string>
}

export function HighlightedText({
  text,
  segments,
  wordPositions = [] as TranscriptionWordPosition[],
  anchors,
  highlightInfo,
  mode,
  onElementClick,
  onWordClick,
  flashingType,
  isReference,
  currentSource = '',
  preserveSegments = false,
  linePositions = [],
  currentTime = 0,
  referenceCorrections = new Map(),
  gaps = [],
  flashingHandler,
  corrections = [],
  activeGapWordIds,
  reviewMode = false,
  onRevertCorrection,
  onEditCorrection,
  onAcceptCorrection,
  onShowCorrectionDetail,
  editedWordIds,
}: HighlightedTextProps) {
  const { handleWordClick } = useWordClick({
    mode,
    onElementClick,
    onWordClick,
    isReference,
    currentSource,
    gaps,
    anchors,
    corrections,
  })

  const shouldWordFlash = (
    wordPos: TranscriptionWordPosition | { word: string; id: string }
  ): boolean => {
    if (!flashingType) {
      return false
    }

    if ('type' in wordPos) {
      // Add handler-specific flashing
      if (flashingType === 'handler' && flashingHandler) {
        const shouldFlash = corrections.some(
          (correction) =>
            correction.handler === flashingHandler &&
            (correction.corrected_word_id === wordPos.word.id ||
              correction.word_id === wordPos.word.id)
        )
        return shouldFlash
      }

      const gap = wordPos.sequence as GapSequence
      const isCorrected =
        corrections.some(
          (correction) =>
            (correction.word_id === wordPos.word.id ||
              correction.corrected_word_id === wordPos.word.id) &&
            gap?.transcribed_word_ids?.includes(correction.word_id)
        ) || wordPos.isCorrected

      return Boolean(
        (flashingType === 'anchor' && wordPos.type === 'anchor') ||
          (flashingType === 'corrected' && isCorrected) ||
          (flashingType === 'uncorrected' &&
            wordPos.type === 'gap' &&
            !isCorrected &&
            // If highlightInfo specifies a gap, only flash that gap's words
            (!highlightInfo?.sequence ||
              (highlightInfo.sequence as GapSequence).transcribed_word_ids?.includes(
                wordPos.word.id
              ))) ||
          (flashingType === 'word' &&
            ((highlightInfo?.type === 'anchor' &&
              wordPos.type === 'anchor' &&
              (isReference && currentSource && highlightInfo.sequence
                ? getWordsFromIds(
                    segments || [],
                    (highlightInfo.sequence as AnchorSequence).reference_word_ids[currentSource] ||
                      []
                  ).some((w) => w.id === wordPos.word.id)
                : getWordsFromIds(
                    segments || [],
                    (highlightInfo.sequence as AnchorSequence).transcribed_word_ids
                  ).some((w) => w.id === wordPos.word.id))) ||
              (highlightInfo?.type === 'gap' &&
                wordPos.type === 'gap' &&
                (isReference && currentSource && highlightInfo.sequence
                  ? getWordsFromIds(
                      segments || [],
                      (highlightInfo.sequence as GapSequence).reference_word_ids[currentSource] ||
                        []
                    ).some((w) => w.id === wordPos.word.id)
                  : getWordsFromIds(
                      segments || [],
                      (highlightInfo.sequence as GapSequence).transcribed_word_ids
                    ).some((w) => w.id === wordPos.word.id))) ||
              (highlightInfo?.type === 'correction' &&
                isReference &&
                currentSource &&
                highlightInfo.correction?.reference_positions?.[currentSource]?.toString() ===
                  wordPos.word.id)))
      )
    }
    return false
  }

  const shouldHighlightWord = (
    wordPos: TranscriptionWordPosition | { word: string; id: string }
  ): boolean => {
    if (isReference) return false

    if ('type' in wordPos && currentTime !== undefined && 'start_time' in wordPos.word) {
      const word = wordPos.word as Word
      return (
        word.start_time !== null &&
        word.end_time !== null &&
        currentTime >= word.start_time &&
        currentTime <= word.end_time
      )
    }
    return false
  }

  const handleCopyLine = (lineText: string) => {
    navigator.clipboard.writeText(lineText)
  }

  // Check if we're on mobile using window width (SSR-safe default)
  const isMobile = typeof window !== 'undefined' && window.innerWidth < 640

  const renderContent = () => {
    if (wordPositions && !segments) {
      return wordPositions.map((wordPos, index) => {
        const correction = corrections?.find(
          (c) => c.corrected_word_id === wordPos.word.id || c.word_id === wordPos.word.id
        )

        // Use CorrectedWordWithActions for agentic corrections
        if (correction && correction.handler === 'AgenticCorrector') {
          return (
            <React.Fragment key={wordPos.word.id}>
              <CorrectedWordWithActions
                word={wordPos.word.text}
                originalWord={correction.original_word}
                correction={{
                  originalWord: correction.original_word,
                  handler: correction.handler,
                  confidence: correction.confidence,
                  source: correction.source,
                  reason: correction.reason,
                }}
                shouldFlash={shouldWordFlash(wordPos)}
                showActions={reviewMode && !isMobile}
                onRevert={() => onRevertCorrection?.(wordPos.word.id)}
                onEdit={() => onEditCorrection?.(wordPos.word.id)}
                onAccept={() => onAcceptCorrection?.(wordPos.word.id)}
                id={`word-${wordPos.word.id}`}
                onClick={() => {
                  if (isMobile) {
                    onShowCorrectionDetail?.(wordPos.word.id)
                  } else {
                    handleWordClick(
                      wordPos.word.text,
                      wordPos.word.id,
                      wordPos.type === 'anchor' ? (wordPos.sequence as AnchorSequence) : undefined,
                      wordPos.type === 'gap' ? (wordPos.sequence as GapSequence) : undefined
                    )
                  }
                }}
              />
              {index < wordPositions.length - 1 && ' '}
            </React.Fragment>
          )
        }

        // Default rendering with WordComponent
        return (
          <React.Fragment key={wordPos.word.id}>
            <WordComponent
              word={wordPos.word.text}
              shouldFlash={shouldWordFlash(wordPos)}
              isAnchor={wordPos.type === 'anchor'}
              isCorrectedGap={wordPos.isCorrected}
              isUncorrectedGap={wordPos.type === 'gap' && !wordPos.isCorrected}
              isCurrentlyPlaying={shouldHighlightWord(wordPos)}
              isActiveGap={activeGapWordIds?.has(wordPos.word.id)}
              isUserEdited={editedWordIds?.has(wordPos.word.id)}
              id={`word-${wordPos.word.id}`}
              onClick={() =>
                handleWordClick(
                  wordPos.word.text,
                  wordPos.word.id,
                  wordPos.type === 'anchor' ? (wordPos.sequence as AnchorSequence) : undefined,
                  wordPos.type === 'gap' ? (wordPos.sequence as GapSequence) : undefined
                )
              }
              correction={
                correction
                  ? {
                      originalWord: correction.original_word,
                      handler: correction.handler,
                      confidence: correction.confidence,
                      source: correction.source,
                      reason: correction.reason,
                    }
                  : null
              }
            />
            {index < wordPositions.length - 1 && ' '}
          </React.Fragment>
        )
      })
    } else if (segments) {
      return segments.map((segment) => (
        <div key={segment.id} className="flex items-start mb-0">
          <div className="flex-1">
            {segment.words.map((word, wordIndex) => {
              const wordPos = wordPositions.find(
                (pos: TranscriptionWordPosition) => pos.word.id === word.id
              )

              const anchor =
                wordPos?.type === 'anchor'
                  ? anchors?.find((a) =>
                      (a.reference_word_ids[currentSource] || []).includes(word.id)
                    )
                  : undefined

              const hasCorrection = referenceCorrections.has(word.id)
              const isUncorrectedGap = wordPos?.type === 'gap' && !hasCorrection

              const sequence =
                wordPos?.type === 'gap' ? (wordPos.sequence as GapSequence) : undefined

              const correction = corrections?.find(
                (c) => c.corrected_word_id === word.id || c.word_id === word.id
              )

              // Use CorrectedWordWithActions for agentic corrections
              if (correction && correction.handler === 'AgenticCorrector') {
                return (
                  <React.Fragment key={word.id}>
                    <CorrectedWordWithActions
                      word={word.text}
                      originalWord={correction.original_word}
                      correction={{
                        originalWord: correction.original_word,
                        handler: correction.handler,
                        confidence: correction.confidence,
                        source: correction.source,
                        reason: correction.reason,
                      }}
                      shouldFlash={shouldWordFlash(wordPos || { word: word.text, id: word.id })}
                      showActions={reviewMode && !isMobile}
                      onRevert={() => onRevertCorrection?.(word.id)}
                      onEdit={() => onEditCorrection?.(word.id)}
                      onAccept={() => onAcceptCorrection?.(word.id)}
                      id={`word-${word.id}`}
                      onClick={() => {
                        if (isMobile) {
                          onShowCorrectionDetail?.(word.id)
                        } else {
                          handleWordClick(word.text, word.id, anchor, sequence)
                        }
                      }}
                    />
                    {wordIndex < segment.words.length - 1 && ' '}
                  </React.Fragment>
                )
              }

              const correctionInfo = correction
                ? {
                    originalWord: correction.original_word,
                    handler: correction.handler,
                    confidence: correction.confidence,
                    source: correction.source,
                    reason: correction.reason,
                  }
                : null

              return (
                <React.Fragment key={word.id}>
                  <WordComponent
                    word={word.text}
                    shouldFlash={shouldWordFlash(wordPos || { word: word.text, id: word.id })}
                    isAnchor={Boolean(anchor)}
                    isCorrectedGap={hasCorrection}
                    isUncorrectedGap={isUncorrectedGap}
                    isCurrentlyPlaying={shouldHighlightWord(
                      wordPos || { word: word.text, id: word.id }
                    )}
                    isActiveGap={activeGapWordIds?.has(word.id)}
                    isUserEdited={editedWordIds?.has(word.id)}
                    id={`word-${word.id}`}
                    onClick={() => handleWordClick(word.text, word.id, anchor, sequence)}
                    correction={correctionInfo}
                  />
                  {wordIndex < segment.words.length - 1 && ' '}
                </React.Fragment>
              )
            })}
          </div>
        </div>
      ))
    } else if (text) {
      const lines = text.split('\n')
      let wordCount = 0

      return lines.map((line, lineIndex) => {
        const currentLinePosition = linePositions?.find((pos) => pos.position === wordCount)
        if (currentLinePosition?.isEmpty) {
          wordCount++
          return (
            <div key={`empty-${lineIndex}`} className="flex items-start mb-0 leading-none">
              <span className="text-muted-foreground w-8 min-w-[2em] text-right mr-2 select-none font-mono pt-[1px] text-[0.8rem] leading-none">
                {currentLinePosition.lineNumber}
              </span>
              <div className="w-[18px]" />
              <div className="flex-1 h-4" />
            </div>
          )
        }

        const words = line.split(' ')
        const lineWords: React.ReactNode[] = []

        words.forEach((word, wordIndex) => {
          if (word === '') return null
          if (/^\s+$/.test(word)) {
            return lineWords.push(<span key={`space-${lineIndex}-${wordIndex}`}> </span>)
          }

          const wordId = `${currentSource}-word-${wordCount}`
          wordCount++

          const anchor = currentSource
            ? anchors?.find((a) => a.reference_word_ids[currentSource]?.includes(wordId))
            : undefined

          const hasCorrection = referenceCorrections.has(wordId)

          lineWords.push(
            <WordComponent
              key={wordId}
              word={word}
              shouldFlash={shouldWordFlash({ word, id: wordId })}
              isAnchor={Boolean(anchor)}
              isCorrectedGap={hasCorrection}
              isUncorrectedGap={false}
              isCurrentlyPlaying={shouldHighlightWord({ word, id: wordId })}
              onClick={() => handleWordClick(word, wordId, anchor, undefined)}
            />
          )
        })

        return (
          <div key={`line-${lineIndex}`} className="flex items-start mb-0 leading-none">
            <span className="text-muted-foreground w-8 min-w-[2em] text-right mr-2 select-none font-mono pt-[1px] text-[0.8rem] leading-none">
              {currentLinePosition?.lineNumber ?? lineIndex}
            </span>
            <Button
              variant="ghost"
              size="icon"
              className="h-[18px] w-[18px] min-h-0 min-w-0 p-[1px] mr-1"
              onClick={() => handleCopyLine(line)}
            >
              <Copy className="h-3.5 w-3.5" />
            </Button>
            <div className="flex-1">{lineWords}</div>
          </div>
        )
      })
    }
    return null
  }

  return (
    <div
      className={`font-mono m-0 leading-tight ${preserveSegments ? 'whitespace-normal' : 'whitespace-pre-wrap'}`}
    >
      {renderContent()}
    </div>
  )
}
