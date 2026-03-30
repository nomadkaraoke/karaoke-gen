'use client'

import { useMemo } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Copy } from 'lucide-react'
import { ReferenceViewProps, TranscriptionWordPosition } from '@/lib/lyrics-review/types'
import { calculateReferenceLinePositions } from '@/lib/lyrics-review/utils/referenceLineCalculator'
import { getWordsFromIds } from '@/lib/lyrics-review/utils/wordUtils'
import { SourceSelector } from './shared/SourceSelector'
import { HighlightedText } from './shared/HighlightedText'
import ReferenceEmptyState from './ReferenceEmptyState'

export default function ReferenceView({
  referenceSources,
  anchors,
  onElementClick,
  onWordClick,
  flashingType,
  corrected_segments,
  currentSource,
  onSourceChange,
  highlightInfo,
  mode,
  gaps,
  corrections,
  onAddLyrics,
  onAddLyricsInline,
  onSearchLyrics,
  defaultArtist = '',
  defaultTitle = '',
}: ReferenceViewProps) {
  const availableSources = useMemo(() => Object.keys(referenceSources), [referenceSources])

  const effectiveCurrentSource = useMemo(
    () => currentSource || (availableSources.length > 0 ? availableSources[0] : ''),
    [currentSource, availableSources]
  )

  const referenceWordPositions = useMemo(() => {
    const positions: TranscriptionWordPosition[] = []
    const allPositions = new Map<number, TranscriptionWordPosition[]>()

    // Map anchor words
    anchors?.forEach((anchor) => {
      const position = anchor.reference_positions[effectiveCurrentSource]
      if (position === undefined) return

      if (!allPositions.has(position)) {
        allPositions.set(position, [])
      }

      const referenceWords = getWordsFromIds(
        referenceSources[effectiveCurrentSource].segments,
        anchor.reference_word_ids[effectiveCurrentSource] || []
      )

      referenceWords.forEach((word) => {
        const wordPosition: TranscriptionWordPosition = {
          word: {
            id: word.id,
            text: word.text,
            start_time: word.start_time ?? undefined,
            end_time: word.end_time ?? undefined,
          },
          type: 'anchor',
          sequence: anchor,
          isInRange: true,
        }
        allPositions.get(position)!.push(wordPosition)
      })
    })

    // Map gap words
    gaps?.forEach((gap) => {
      const precedingAnchor = gap.preceding_anchor_id
        ? anchors?.find((a) => a.id === gap.preceding_anchor_id)
        : undefined
      const followingAnchor = gap.following_anchor_id
        ? anchors?.find((a) => a.id === gap.following_anchor_id)
        : undefined

      const position =
        precedingAnchor?.reference_positions[effectiveCurrentSource] ??
        followingAnchor?.reference_positions[effectiveCurrentSource]

      if (position === undefined) return

      const gapPosition = precedingAnchor ? position + 1 : position - 1

      if (!allPositions.has(gapPosition)) {
        allPositions.set(gapPosition, [])
      }

      const referenceWords = getWordsFromIds(
        referenceSources[effectiveCurrentSource].segments,
        gap.reference_word_ids[effectiveCurrentSource] || []
      )

      referenceWords.forEach((word) => {
        const isWordCorrected = corrections?.some(
          (correction) =>
            correction.reference_positions?.[effectiveCurrentSource]?.toString() === word.id &&
            gap.transcribed_word_ids.includes(correction.word_id)
        )

        const wordPosition: TranscriptionWordPosition = {
          word: {
            id: word.id,
            text: word.text,
            start_time: word.start_time ?? undefined,
            end_time: word.end_time ?? undefined,
          },
          type: 'gap',
          sequence: gap,
          isInRange: true,
          isCorrected: isWordCorrected,
        }
        allPositions.get(gapPosition)!.push(wordPosition)
      })
    })

    // Sort by position and flatten
    Array.from(allPositions.entries())
      .sort(([a], [b]) => a - b)
      .forEach(([, words]) => {
        positions.push(...words)
      })

    return positions
  }, [anchors, gaps, effectiveCurrentSource, referenceSources, corrections])

  const { linePositions } = useMemo(
    () => calculateReferenceLinePositions(corrected_segments, anchors, effectiveCurrentSource),
    [corrected_segments, anchors, effectiveCurrentSource]
  )

  const referenceCorrections = useMemo(() => {
    const correctionMap = new Map<string, string>()

    corrections?.forEach((correction) => {
      const referencePosition = correction.reference_positions?.[effectiveCurrentSource]
      if (referencePosition !== undefined) {
        correctionMap.set(referencePosition.toString(), correction.corrected_word)
      }
    })

    return correctionMap
  }, [corrections, effectiveCurrentSource])

  const currentSourceSegments = referenceSources[effectiveCurrentSource]?.segments || []

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text)
  }

  const copyAllReferenceText = () => {
    const allText = currentSourceSegments
      .map((segment) => segment.words.map((w) => w.text).join(' '))
      .join('\n')
    copyToClipboard(allText)
  }

  return (
    <Card className="p-2 relative">
      <CardContent className="p-0">
        <div className="flex justify-between items-center mb-1">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold">Reference Lyrics</h3>
            <Button
              variant="ghost"
              size="icon"
              className="h-5 w-5 min-h-0 min-w-0 p-0.5"
              onClick={copyAllReferenceText}
              title="Copy all reference lyrics"
            >
              <Copy className="h-4 w-4" />
            </Button>
          </div>
          <SourceSelector
            availableSources={availableSources}
            currentSource={effectiveCurrentSource}
            onSourceChange={onSourceChange}
            onAddLyrics={onAddLyrics}
          />
        </div>

        {availableSources.length === 0 && onAddLyricsInline && onSearchLyrics && (
          <ReferenceEmptyState
            defaultArtist={defaultArtist}
            defaultTitle={defaultTitle}
            onAdd={onAddLyricsInline}
            onSearch={onSearchLyrics}
          />
        )}

        <div className="flex flex-col gap-0.5">
          {currentSourceSegments.map((segment, index) => (
            <div key={index} className="flex items-start w-full hover:bg-muted/50 transition-colors">
              <div className="flex items-center gap-0.5 pr-1">
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-[18px] w-[18px] min-h-0 min-w-0 p-[1px]"
                  onClick={() => copyToClipboard(segment.words.map((w) => w.text).join(' '))}
                >
                  <Copy className="h-3.5 w-3.5" />
                </Button>
              </div>
              <div className="flex-1 min-w-0">
                <HighlightedText
                  wordPositions={referenceWordPositions.filter((wp) =>
                    segment.words.some((w) => w.id === wp.word.id)
                  )}
                  segments={[segment]}
                  anchors={anchors}
                  onElementClick={onElementClick}
                  onWordClick={onWordClick}
                  flashingType={flashingType}
                  highlightInfo={highlightInfo}
                  mode={mode}
                  isReference={true}
                  currentSource={effectiveCurrentSource}
                  linePositions={linePositions}
                  referenceCorrections={referenceCorrections}
                  gaps={gaps}
                  preserveSegments={true}
                />
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}
