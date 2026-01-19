'use client'

import { AlertTriangle } from 'lucide-react'
import { LyricsSegment, WordCorrection, AnchorSequence, GapSequence } from '@/lib/lyrics-review/types'
import { COLORS } from '@/lib/lyrics-review/constants'
import { cn } from '@/lib/utils'

interface DurationTimelineViewProps {
  segments: LyricsSegment[]
  corrections: WordCorrection[]
  anchors: AnchorSequence[]
  gaps: GapSequence[]
  onWordClick?: (wordId: string) => void
}

export default function DurationTimelineView({
  segments,
  corrections,
  anchors,
  gaps,
  onWordClick,
}: DurationTimelineViewProps) {
  const timeToPosition = (time: number, startTime: number, endTime: number): number => {
    const duration = endTime - startTime
    if (duration === 0) return 0
    const position = ((time - startTime) / duration) * 100
    return Math.max(0, Math.min(100, position))
  }

  const generateTimelineMarks = (startTime: number, endTime: number) => {
    const marks = []
    const startSecond = Math.floor(startTime)
    const endSecond = Math.ceil(endTime)

    // Generate marks roughly every 2 seconds for readability
    for (let time = startSecond; time <= endSecond; time += 2) {
      if (time >= startTime && time <= endTime) {
        const position = timeToPosition(time, startTime, endTime)
        marks.push(
          <div key={time}>
            <div
              className="absolute w-[1px] h-2 bg-muted-foreground/50 bottom-0"
              style={{ left: `${position}%` }}
            />
            <div
              className="absolute text-[0.65rem] text-muted-foreground bottom-2.5 whitespace-nowrap -translate-x-1/2"
              style={{ left: `${position}%` }}
            >
              {time}s
            </div>
          </div>
        )
      }
    }
    return marks
  }

  return (
    <div className="flex flex-col gap-3 p-2 overflow-x-auto min-w-full">
      {segments.map((segment) => {
        // Calculate segment time range
        const segmentWords = segment.words.filter(
          (w) => w.start_time !== null && w.end_time !== null
        )
        if (segmentWords.length === 0) return null

        const startTime = Math.min(...segmentWords.map((w) => w.start_time!))
        const endTime = Math.max(...segmentWords.map((w) => w.end_time!))

        return (
          <div key={segment.id} className="flex flex-col gap-1 min-w-full">
            {/* Timeline ruler */}
            <div className="relative h-5 border-b border-border mb-1">
              {generateTimelineMarks(startTime, endTime)}
            </div>

            {/* Words bar */}
            <div
              className="relative h-[60px] flex items-stretch min-w-full bg-background rounded mb-2"
              style={{ touchAction: 'pan-y' }}
            >
              {segment.words.map((word, wordIndex) => {
                if (word.start_time === null || word.end_time === null) return null

                const leftPosition = timeToPosition(word.start_time, startTime, endTime)
                const rightPosition = timeToPosition(word.end_time, startTime, endTime)
                const width = rightPosition - leftPosition
                const duration = word.end_time - word.start_time
                const isLong = duration > 2.0

                // Check if this word is corrected
                const correction = corrections.find(
                  (c) => c.corrected_word_id === word.id || c.word_id === word.id
                )

                // Check if anchor
                const isAnchor = anchors.some((a) => a.transcribed_word_ids.includes(word.id))

                // Check if uncorrected gap
                const isGap =
                  gaps.some((g) => g.transcribed_word_ids.includes(word.id)) && !correction

                // Determine background color
                let bgColor = 'hsl(var(--secondary))'
                if (isAnchor) bgColor = COLORS.anchor
                else if (correction) bgColor = COLORS.corrected
                else if (isGap) bgColor = COLORS.uncorrectedGap

                return (
                  <div
                    key={`${segment.id}-${wordIndex}`}
                    className={cn(
                      'absolute h-full flex flex-col justify-center items-center px-1 py-0.5',
                      'border-r border-white/30 text-[0.75rem] cursor-pointer transition-all',
                      'hover:opacity-80 hover:scale-[1.02] hover:z-10',
                      'overflow-hidden text-ellipsis whitespace-nowrap min-w-[20px]',
                      isLong && 'border-2 border-red-500 shadow-[0_0_4px_rgba(244,67,54,0.5)]'
                    )}
                    style={{
                      left: `${leftPosition}%`,
                      width: `${width}%`,
                      backgroundColor: bgColor,
                    }}
                    onClick={() => onWordClick?.(word.id)}
                  >
                    {isLong && (
                      <AlertTriangle className="absolute -top-0.5 -right-0.5 h-4 w-4 text-red-500" />
                    )}
                    {correction && (
                      <span className="text-[0.65rem] text-muted-foreground leading-none mb-0.5 line-through opacity-85 font-medium bg-background/80 px-0.5 rounded-sm">
                        {correction.original_word}
                      </span>
                    )}
                    <span
                      className={cn(
                        'leading-tight overflow-hidden text-ellipsis whitespace-nowrap w-full text-center',
                        correction
                          ? 'text-[0.85rem] font-bold text-green-400'
                          : 'text-[0.75rem] font-medium'
                      )}
                    >
                      {word.text}
                    </span>
                    {duration > 0.1 && (
                      <span className="text-[0.6rem] text-foreground/60 leading-none mt-0.5 font-semibold">
                        {duration.toFixed(1)}s
                      </span>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        )
      })}
    </div>
  )
}
