'use client'

import { useTranslations } from 'next-intl'
import { useState, useMemo } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { Play, Trash2, Type, Clock } from 'lucide-react'
import { HighlightedText } from './shared/HighlightedText'
import { TranscriptionViewProps, TranscriptionWordPosition } from '@/lib/lyrics-review/types'
import { deleteSegment } from '@/lib/lyrics-review/utils/segmentOperations'
import SegmentDetailsModal from './modals/SegmentDetailsModal'
import SingerChip from './SingerChip'
import { resolveSegmentSinger, hasWordOverrides } from '@/lib/lyrics-review/duet'
import { useAudioReady } from '@/lib/lyrics-review/hooks/useAudioReady'
import type { SingerId } from '@/lib/lyrics-review/types'
import { cn } from '@/lib/utils'

export default function TranscriptionView({
  data,
  onElementClick,
  onWordClick,
  flashingType,
  flashingHandler,
  highlightInfo,
  mode,
  onPlaySegment,
  currentTime = 0,
  anchors = [],
  onDataChange,
  reviewMode = false,
  onRevertCorrection,
  onEditCorrection,
  onAcceptCorrection,
  onShowCorrectionDetail,
  activeGapWordIds,
  advancedMode = false,
  onAdvancedModeToggle,
  editedWordIds,
  aiCorrectedWordIds,
  aiOriginalTextByWordId,
  aiEstimatedWordIds,
  isDuet,
  onSegmentSingerChange,
  onSegmentFocus,
}: TranscriptionViewProps) {
  const t = useTranslations('lyricsReview.transcription')
  const tHeader = useTranslations('lyricsReview.header')
  const { ready: audioReady } = useAudioReady()
  const [selectedSegmentIndex, setSelectedSegmentIndex] = useState<number | null>(null)

  // Timing warnings (shown in both Simple and Advanced): any word longer than
  // 2s, and any gap longer than 2s between consecutive words in a segment.
  // These are the issues the old Timeline view existed to surface.
  // Timing maps. longWord/longGap drive the warnings (both modes). In Advanced,
  // timelineGrow (= duration) and timelineGap (= gap before) become flex weights
  // so each segment's words fill the full width proportional to their timing.
  const { longWordByWordId, longGapAfterByWordId, timelineGrowByWordId, timelineGapByWordId } =
    useMemo(() => {
      const TIMING_WARNING_THRESHOLD_S = 2
      const longWord = new Map<string, number>()
      const longGap = new Map<string, number>()
      const grow = new Map<string, number>()
      const gapBefore = new Map<string, number>()
      for (const seg of data.corrected_segments) {
        const ws = seg.words
        for (let i = 0; i < ws.length; i++) {
          const w = ws[i]
          if (w.start_time != null && w.end_time != null) {
            const dur = Math.max(0, w.end_time - w.start_time)
            if (dur > TIMING_WARNING_THRESHOLD_S) longWord.set(w.id, dur)
            grow.set(w.id, dur)
          }
          const next = ws[i + 1]
          if (next && w.end_time != null && next.start_time != null) {
            const gap = next.start_time - w.end_time
            if (gap > TIMING_WARNING_THRESHOLD_S) longGap.set(w.id, gap)
          }
          const prev = ws[i - 1]
          if (prev && prev.end_time != null && w.start_time != null) {
            const gap = w.start_time - prev.end_time
            if (gap > 0.15) gapBefore.set(w.id, gap)
          }
        }
      }
      return {
        longWordByWordId: longWord,
        longGapAfterByWordId: longGap,
        timelineGrowByWordId: grow,
        timelineGapByWordId: gapBefore,
      }
    }, [data.corrected_segments])

  const handleDeleteSegment = (segmentIndex: number) => {
    if (onDataChange) {
      const updatedData = deleteSegment(data, segmentIndex)
      onDataChange(updatedData)
    }
  }

  return (
    <Card className="p-2">
      <CardContent className="p-0">
        <div className="flex justify-between items-center mb-1">
          <h3 className="text-sm font-semibold">{t('syncedLyrics')}</h3>
          <ToggleGroup
            type="single"
            value={advancedMode ? 'advanced' : 'simple'}
            onValueChange={(value) => value && onAdvancedModeToggle?.(value === 'advanced')}
            className="h-7"
          >
            <ToggleGroupItem value="simple" aria-label="simple view" className="h-7 px-2.5 text-[0.75rem]">
              <Type className="h-3.5 w-3.5 mr-1" />
              {t('simple')}
            </ToggleGroupItem>
            <ToggleGroupItem value="advanced" aria-label="advanced view" className="h-7 px-2.5 text-[0.75rem]">
              <Clock className="h-3.5 w-3.5 mr-1.5" />
              {t('advanced')}
            </ToggleGroupItem>
          </ToggleGroup>
        </div>

        {(
          // Advanced rows are full-width pill timelines, so give them a bit
          // more breathing room between lines to match Simple's rhythm.
          <div className={cn('flex flex-col', advancedMode ? 'gap-2' : 'gap-0.5')}>
            {data.corrected_segments.map((segment, segmentIndex) => {
              const segmentWords: TranscriptionWordPosition[] = segment.words.map((word) => {
                const correction = data.corrections?.find(
                  (c) => c.corrected_word_id === word.id || c.word_id === word.id
                )

                const anchor = data.anchor_sequences?.find((a) =>
                  a.transcribed_word_ids.includes(word.id)
                )

                const gap = data.gap_sequences?.find((g) => {
                  const inTranscribed = g.transcribed_word_ids.includes(word.id)
                  const inReference = Object.values(g.reference_word_ids).some((ids) =>
                    ids.includes(word.id)
                  )
                  const isCorrection = data.corrections.some(
                    (c) =>
                      (c.corrected_word_id === word.id || c.word_id === word.id) &&
                      g.transcribed_word_ids.includes(c.word_id)
                  )
                  return inTranscribed || inReference || isCorrection
                })

                return {
                  word: {
                    id: word.id,
                    text: word.text,
                    start_time: word.start_time ?? undefined,
                    end_time: word.end_time ?? undefined,
                  },
                  type: anchor ? 'anchor' : gap ? 'gap' : 'other',
                  sequence: anchor || gap,
                  isInRange: true,
                  isCorrected: Boolean(correction),
                }
              })

              // Segments with an AI correction need headroom for the
              // original-text bubble that floats above the corrected word.
              const segmentHasAiCorrection = segment.words.some((w) =>
                aiCorrectedWordIds?.has(w.id)
              )

              const segmentSinger: SingerId = resolveSegmentSinger(segment)
              const rowTintClass = isDuet
                ? segmentSinger === 1 ? 'bg-gradient-to-r from-blue-500/10 to-transparent' :
                  segmentSinger === 2 ? 'bg-gradient-to-r from-pink-500/10 to-transparent' :
                  'bg-gradient-to-r from-yellow-500/10 to-transparent'
                : ''

              return (
                <div
                  key={segment.id}
                  tabIndex={isDuet && onSegmentFocus ? 0 : undefined}
                  onFocus={isDuet && onSegmentFocus ? () => onSegmentFocus(segmentIndex) : undefined}
                  onBlur={isDuet && onSegmentFocus ? () => onSegmentFocus(null) : undefined}
                  className={cn(
                    'flex items-start w-full hover:bg-muted/50 transition-colors',
                    segmentHasAiCorrection && 'pt-5',
                    rowTintClass,
                  )}
                >
                  {/* Segment controls */}
                  <div className="flex items-center gap-0.5 pr-1" style={{ minWidth: advancedMode ? '2.5em' : undefined }}>
                    {advancedMode && (
                      <span
                        className="text-muted-foreground w-[1.8em] text-right mr-1 select-none font-mono text-[0.8rem] leading-tight cursor-pointer hover:underline"
                        onClick={() => setSelectedSegmentIndex(segmentIndex)}
                      >
                        {segmentIndex}
                      </span>
                    )}
                    {advancedMode && (
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-[18px] w-[18px] min-h-0 min-w-0 p-[1px] text-destructive hover:text-destructive"
                        onClick={() => handleDeleteSegment(segmentIndex)}
                        title={t('deleteSegment')}
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </Button>
                    )}
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

                  {/* Singer chip (duet mode only) */}
                  {isDuet && onSegmentSingerChange && (
                    <SingerChip
                      singer={segmentSinger}
                      hasOverrides={hasWordOverrides(segment)}
                      onChange={(next) => onSegmentSingerChange(segmentIndex, next)}
                      className="mr-1 flex-shrink-0 self-center"
                    />
                  )}

                  {/* Text content */}
                  <div className="flex-1 min-w-0">
                    <HighlightedText
                      wordPositions={segmentWords}
                      anchors={anchors}
                      onElementClick={onElementClick}
                      onWordClick={onWordClick}
                      flashingType={flashingType}
                      flashingHandler={flashingHandler}
                      highlightInfo={highlightInfo}
                      mode={mode}
                      preserveSegments={true}
                      currentTime={currentTime}
                      gaps={data.gap_sequences}
                      corrections={data.corrections}
                      activeGapWordIds={activeGapWordIds}
                      reviewMode={reviewMode}
                      onRevertCorrection={onRevertCorrection}
                      onEditCorrection={onEditCorrection}
                      onAcceptCorrection={onAcceptCorrection}
                      onShowCorrectionDetail={onShowCorrectionDetail}
                      editedWordIds={editedWordIds}
                      aiCorrectedWordIds={aiCorrectedWordIds}
                      aiOriginalTextByWordId={aiOriginalTextByWordId}
                      aiEstimatedWordIds={aiEstimatedWordIds}
                      longWordByWordId={longWordByWordId}
                      longGapAfterByWordId={longGapAfterByWordId}
                      onSeekPlay={audioReady ? onPlaySegment : undefined}
                      timelineLayout={advancedMode}
                      timelineGrowByWordId={advancedMode ? timelineGrowByWordId : undefined}
                      timelineGapByWordId={advancedMode ? timelineGapByWordId : undefined}
                    />
                  </div>
                </div>
              )
            })}
          </div>
        )}

        <SegmentDetailsModal
          open={selectedSegmentIndex !== null}
          onClose={() => setSelectedSegmentIndex(null)}
          segment={
            selectedSegmentIndex !== null ? data.corrected_segments[selectedSegmentIndex] : null
          }
          segmentIndex={selectedSegmentIndex}
        />
      </CardContent>
    </Card>
  )
}
