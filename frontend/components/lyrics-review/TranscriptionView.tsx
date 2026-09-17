'use client'

import { useTranslations } from 'next-intl'
import { useState, useMemo } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { Play, Trash2, Type, Clock, AudioWaveform } from 'lucide-react'
import { HighlightedText } from './shared/HighlightedText'
import { TranscriptionViewProps, TranscriptionWordPosition } from '@/lib/lyrics-review/types'
import { deleteSegment } from '@/lib/lyrics-review/utils/segmentOperations'
import { computeContextWordsBySegment } from '@/lib/lyrics-review/utils/contextWords'
import { buildSegmentDecorations } from '@/lib/lyrics-review/utils/wordDecorations'
import { TIMELINE_PAD_SECONDS } from './TimelineEditor'
import WaveformSegmentRow from './WaveformSegmentRow'
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
  viewMode = 'simple',
  onViewModeChange,
  onCommitSegment,
  onEditSegment,
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

  // Advanced (flex-pill) layout is used by both the Advanced and Waveforms modes for the
  // left-hand controls / spacing; Waveforms additionally swaps the row body for an inline
  // timeline. Keeping `advancedMode` derived avoids churning the HighlightedText wiring.
  const advancedMode = viewMode === 'advanced'
  const waveformsMode = viewMode === 'waveforms'

  // Greyed read-only neighbour words per segment, for the Waveforms inline timeline padding.
  // Only computed in Waveforms mode. `data.corrected_segments` here is already offset-applied
  // (the parent passes displayData), so the context words align with the drawn bars.
  const contextWordsBySegment = useMemo(
    () =>
      waveformsMode
        ? computeContextWordsBySegment(data.corrected_segments, TIMELINE_PAD_SECONDS)
        : null,
    [waveformsMode, data.corrected_segments]
  )

  // Per-segment bar colours + AI ghost text for Waveforms mode. Memoised so a playback tick
  // (currentTime changing every ~100ms) doesn't reclassify every word each frame.
  const decorationsBySegment = useMemo(() => {
    if (!waveformsMode) return null
    const map = new Map<string, ReturnType<typeof buildSegmentDecorations>>()
    for (const segment of data.corrected_segments) {
      map.set(
        segment.id,
        buildSegmentDecorations(data, segment, {
          editedWordIds,
          aiCorrectedWordIds,
          aiOriginalTextByWordId,
        })
      )
    }
    return map
  }, [waveformsMode, data, editedWordIds, aiCorrectedWordIds, aiOriginalTextByWordId])

  // With no reference lyrics, nothing classifies words as anchor/gap/corrected,
  // so every pill would render uncoloured and the line loses the colour-scanning
  // we rely on. Fall back to the gap-orange highlight for plain words in that
  // case (applies to both Simple and Advanced via HighlightedText).
  const hasReferenceLyrics = Object.keys(data.reference_lyrics ?? {}).length > 0

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
            value={viewMode}
            onValueChange={(value) => value && onViewModeChange?.(value as typeof viewMode)}
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
            <ToggleGroupItem value="waveforms" aria-label="waveforms view" className="h-7 px-2.5 text-[0.75rem]">
              <AudioWaveform className="h-3.5 w-3.5 mr-1.5" />
              {t('waveforms')}
            </ToggleGroupItem>
          </ToggleGroup>
        </div>

        {waveformsMode ? (
          // Waveforms mode: an inline, compact copy of the Edit Segment timeline for every
          // segment — resizable word bars over the vocal waveform, with buffer padding +
          // boundary lines + greyed neighbour words. Lets the reviewer spot mis-timed words
          // (e.g. an over-long trailing word) at a glance without opening a modal per line.
          <div className="flex flex-col gap-[5px]">
            {data.corrected_segments.map((segment, segmentIndex) => (
              <WaveformSegmentRow
                key={segment.id}
                segment={segment}
                segmentIndex={segmentIndex}
                contextWords={contextWordsBySegment?.get(segmentIndex) ?? []}
                currentTime={currentTime}
                wordDecorations={decorationsBySegment?.get(segment.id) ?? new Map()}
                onCommit={(idx, updated) => onCommitSegment?.(idx, updated)}
                onPlaySegment={onPlaySegment}
                onEditSegment={(idx) => onEditSegment?.(idx)}
                onDeleteSegment={handleDeleteSegment}
              />
            ))}
          </div>
        ) : (
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
                      noReferenceFallback={!hasReferenceLyrics}
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
