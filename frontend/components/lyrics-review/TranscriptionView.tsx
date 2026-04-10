'use client'

import { useTranslations } from 'next-intl'
import { useState } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { Badge } from '@/components/ui/badge'
import { Play, Trash2, Type, Clock } from 'lucide-react'
import { HighlightedText } from './shared/HighlightedText'
import { TranscriptionViewProps, TranscriptionWordPosition } from '@/lib/lyrics-review/types'
import { deleteSegment } from '@/lib/lyrics-review/utils/segmentOperations'
import SegmentDetailsModal from './modals/SegmentDetailsModal'
import DurationTimelineView from './DurationTimelineView'

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
}: TranscriptionViewProps) {
  const t = useTranslations('lyricsReview.transcription')
  const [selectedSegmentIndex, setSelectedSegmentIndex] = useState<number | null>(null)
  const [viewMode, setViewMode] = useState<'text' | 'duration'>('text')

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
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold">{t('syncedLyrics')}</h3>
            <Badge
              variant={advancedMode ? 'default' : 'outline'}
              className="cursor-pointer hover:opacity-80 transition-opacity text-[0.65rem] h-5 px-1.5"
              onClick={() => onAdvancedModeToggle?.(!advancedMode)}
            >
              {advancedMode ? t('advanced') : t('simple')}
            </Badge>
          </div>
          <ToggleGroup
            type="single"
            value={viewMode}
            onValueChange={(value) => value && setViewMode(value as 'text' | 'duration')}
            className="h-7"
          >
            <ToggleGroupItem value="text" aria-label="text view" className="h-7 px-2.5 text-[0.75rem]">
              <Type className="h-3.5 w-3.5 mr-1" />
              {t('text')}
            </ToggleGroupItem>
            <ToggleGroupItem
              value="duration"
              aria-label="duration view"
              className="h-7 px-2.5 text-[0.75rem]"
            >
              <Clock className="h-3.5 w-3.5 mr-1.5" />
              {t('timeline')}
            </ToggleGroupItem>
          </ToggleGroup>
        </div>

        {viewMode === 'duration' ? (
          <DurationTimelineView
            segments={data.corrected_segments}
            corrections={data.corrections || []}
            anchors={data.anchor_sequences || []}
            gaps={data.gap_sequences || []}
            onWordClick={(wordId) => {
              // Find word in segments
              for (const segment of data.corrected_segments) {
                const word = segment.words.find((w) => w.id === wordId)
                if (word) {
                  onWordClick?.({
                    word_id: wordId,
                    type: 'other',
                    anchor: undefined,
                    gap: undefined,
                  })
                  break
                }
              }
            }}
          />
        ) : (
          <div className="flex flex-col gap-0.5">
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

              return (
                <div
                  key={segment.id}
                  className="flex items-start w-full hover:bg-muted/50 transition-colors"
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
                        title={t('playSegment')}
                      >
                        <Play className="h-3.5 w-3.5" />
                      </Button>
                    )}
                  </div>

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
