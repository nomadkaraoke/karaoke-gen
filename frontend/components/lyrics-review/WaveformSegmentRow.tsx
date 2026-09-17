'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import { Play, Trash2 } from 'lucide-react'
import { LyricsSegment, Word } from '@/lib/lyrics-review/types'
import TimelineEditor from './TimelineEditor'
import { recomputeSegmentFromWords } from '@/lib/lyrics-review/utils/segmentTiming'
import { WordDecoration } from '@/lib/lyrics-review/utils/wordDecorations'
import { useAudioReady } from '@/lib/lyrics-review/hooks/useAudioReady'

interface WaveformSegmentRowProps {
  segment: LyricsSegment
  segmentIndex: number
  /** Neighbouring segments' words within the padded window, greyed/read-only for context. */
  contextWords: Word[]
  /** Per-word bar colour + AI-correction ghost text, keyed by word id. */
  wordDecorations: Map<string, WordDecoration>
  currentTime?: number
  /** Fires on drag release with the segment's recomputed word timings + bounds. */
  onCommit: (segmentIndex: number, updatedSegment: LyricsSegment) => void
  onPlaySegment?: (time: number) => void
  /** Open the full Edit modal for structural edits (text / split / merge / add / delete). */
  onEditSegment: (segmentIndex: number) => void
  /** Delete the whole segment (matches the Advanced view's trash control). */
  onDeleteSegment: (segmentIndex: number) => void
}

/**
 * A single row of the Waveforms review mode: a compact, inline copy of the Edit Segment
 * timeline (resizable word bars over the vocal waveform, with buffer padding + boundary
 * lines + greyed neighbour words). Drag/resize edits the word timing and persists on
 * release; the segment number / pencil open the modal for everything else.
 *
 * Word edits are held in local state during a drag so only this row re-renders (not the
 * whole list) and so a mousemove-per-pixel doesn't flood the undo history — we commit once
 * on drag end.
 */
export default function WaveformSegmentRow({
  segment,
  segmentIndex,
  contextWords,
  wordDecorations,
  currentTime,
  onCommit,
  onPlaySegment,
  onEditSegment,
  onDeleteSegment,
}: WaveformSegmentRowProps) {
  const t = useTranslations('lyricsReview.transcription')
  const tHeader = useTranslations('lyricsReview.header')
  const { ready: audioReady } = useAudioReady()

  // Local working copy of the words, re-seeded whenever the segment changes upstream
  // (undo/redo, modal save, auto-correct). `segment` is referentially stable between
  // unrelated re-renders (e.g. playback time ticks), so this does not clobber an
  // in-progress drag — commits only happen on drag release, when no drag is active.
  const [words, setWords] = useState<Word[]>(segment.words)
  // Mirror of `words` that is always current, so handleCommit (fired from a mouse-up event
  // handler) can read the latest timings without listing `words` as a dep — and, crucially,
  // without calling the parent's setState from inside a setWords updater (which React runs
  // during render, triggering the "setState while rendering another component" warning).
  const wordsRef = useRef(words)
  useEffect(() => {
    setWords(segment.words)
    wordsRef.current = segment.words
  }, [segment])

  const handleWordUpdate = useCallback((index: number, updates: Partial<Word>) => {
    const next = wordsRef.current.map((w, i) => (i === index ? { ...w, ...updates } : w))
    wordsRef.current = next
    setWords(next)
  }, [])

  const handleCommit = useCallback(() => {
    onCommit(segmentIndex, recomputeSegmentFromWords(segment, wordsRef.current))
  }, [segment, segmentIndex, onCommit])

  const startTime = segment.start_time ?? 0
  const endTime = segment.end_time ?? startTime + 1
  const hasTimedWords = words.some((w) => w.start_time !== null && w.end_time !== null)

  return (
    <div className="flex items-start w-full gap-1">
      {/* Left controls: index (opens modal), play, edit */}
      {/* Left controls match the Advanced view: index, delete, play. */}
      <div className="flex items-center gap-0.5 pr-1 h-[20px]" style={{ minWidth: '2.5em' }}>
        <span
          className="text-muted-foreground w-[1.8em] text-right mr-1 select-none font-mono text-[0.8rem] leading-tight cursor-pointer hover:underline"
          onClick={() => onEditSegment(segmentIndex)}
          title={t('editSegment', { index: segmentIndex })}
        >
          {segmentIndex}
        </span>
        <Button
          variant="ghost"
          size="icon"
          className="h-[18px] w-[18px] min-h-0 min-w-0 p-[1px] text-destructive hover:text-destructive"
          onClick={() => onDeleteSegment(segmentIndex)}
          title={t('deleteSegment')}
        >
          <Trash2 className="h-3.5 w-3.5" />
        </Button>
        {segment.start_time !== null && (
          <Button
            variant="ghost"
            size="icon"
            className="h-[18px] w-[18px] min-h-0 min-w-0 p-[1px]"
            onClick={() => onPlaySegment?.(segment.start_time!)}
            disabled={!audioReady}
            title={audioReady ? t('playSegment') : tHeader('audioStillLoading')}
          >
            <Play className="h-3.5 w-3.5" />
          </Button>
        )}
      </div>

      {/* Inline timeline (waveform + resizable word bars), ruler hidden for compactness */}
      <div className="flex-1 min-w-0">
        {hasTimedWords ? (
          <TimelineEditor
            words={words}
            contextWords={contextWords}
            startTime={startTime}
            endTime={endTime}
            onWordUpdate={handleWordUpdate}
            onCommit={handleCommit}
            onWordClick={() => onEditSegment(segmentIndex)}
            currentTime={currentTime}
            onPlaySegment={onPlaySegment}
            showRuler={false}
            compact
            wordDecorations={wordDecorations}
          />
        ) : (
          <div
            className="text-sm text-muted-foreground italic px-2 py-2 cursor-pointer hover:underline"
            onClick={() => onEditSegment(segmentIndex)}
          >
            {segment.text || t('untimedSegment')}
          </div>
        )}
      </div>
    </div>
  )
}
