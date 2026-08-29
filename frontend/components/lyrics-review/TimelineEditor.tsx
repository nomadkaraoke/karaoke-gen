'use client'

import { useRef, useState } from 'react'
import { Word } from '@/lib/lyrics-review/types'
import { cn } from '@/lib/utils'
import { WaveformVisualizer } from './WaveformVisualizer'
import { VocalsAudioDataLoaderContext } from './VocalsAudioDataLoader'

// Seconds of context shown (and playable) on each side of the segment in the Edit Segment
// timeline. Exported so the modal's play/stop range matches the visible padded view.
export const TIMELINE_PAD_SECONDS = 1

interface TimelineEditorProps {
  words: Word[]
  /** Neighbouring segments' words, drawn greyed/read-only where they fall in the padded view. */
  contextWords?: Word[]
  startTime: number
  endTime: number
  onWordUpdate: (index: number, updates: Partial<Word>) => void
  onUnsyncWord?: (index: number) => void
  currentTime?: number
  onPlaySegment?: (time: number) => void
  showPlaybackIndicator?: boolean
}

export default function TimelineEditor({
  words,
  contextWords,
  startTime,
  endTime,
  onWordUpdate,
  onUnsyncWord,
  currentTime = 0,
  onPlaySegment,
  showPlaybackIndicator = true,
}: TimelineEditorProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [dragState, setDragState] = useState<{
    wordIndex: number
    type: 'move' | 'resize-left' | 'resize-right'
    initialX: number
    initialTime: number
    word: Word
  } | null>(null)

  const MIN_DURATION = 0.1 // Minimum word duration in seconds

  // Show a little context on each side of the segment (greyed out) so the first word can be
  // dragged/synced earlier and the last word later, and so the surrounding waveform + any word
  // blocks that spill just outside the segment stay visible. The whole timeline maps this padded
  // "view domain" to 0–100%; the segment itself is the un-shaded band in the middle.
  const viewStart = Math.max(0, startTime - TIMELINE_PAD_SECONDS)
  const viewEnd = endTime + TIMELINE_PAD_SECONDS
  const viewDuration = viewEnd - viewStart

  const checkCollision = (
    proposedStart: number,
    proposedEnd: number,
    currentIndex: number,
    isResize: boolean
  ): boolean => {
    if (isResize) {
      // Compare against the nearest *timed* neighbours, not just array neighbours:
      // unsynchronized words (null timestamps) aren't drawn on the timeline, so an
      // adjacent null-timed word must be skipped to reach the real neighbour bar.
      const nextWord = words
        .slice(currentIndex + 1)
        .find((word) => word.start_time !== null && word.end_time !== null)
      if (nextWord && nextWord.start_time !== null && proposedEnd > nextWord.start_time) {
        return true
      }

      const previousWord = words
        .slice(0, currentIndex)
        .reverse()
        .find((word) => word.start_time !== null && word.end_time !== null)
      if (previousWord && previousWord.end_time !== null && proposedStart < previousWord.end_time) {
        return true
      }

      return false
    }

    return words.some((word, index) => {
      if (index === currentIndex) return false
      if (word.start_time === null || word.end_time === null) return false

      return (
        (proposedStart >= word.start_time && proposedStart <= word.end_time) ||
        (proposedEnd >= word.start_time && proposedEnd <= word.end_time) ||
        (proposedStart <= word.start_time && proposedEnd >= word.end_time)
      )
    })
  }

  const timeToPosition = (time: number): number => {
    const position = ((time - viewStart) / viewDuration) * 100
    return Math.max(0, Math.min(100, position))
  }

  const generateTimelineMarks = () => {
    const marks = []
    const startSecond = Math.floor(viewStart)
    const endSecond = Math.ceil(viewEnd)

    for (let time = startSecond; time <= endSecond; time++) {
      if (time >= viewStart && time <= viewEnd) {
        const position = timeToPosition(time)
        marks.push(
          <div key={time}>
            <div
              className="absolute top-5 w-[1px] h-[18px] bg-muted-foreground"
              style={{ left: `${position}%` }}
            />
            <div
              className="absolute top-[5px] -translate-x-1/2 text-[0.8rem] font-bold text-foreground bg-card px-1 rounded-sm"
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

  const handleMouseDown = (
    e: React.MouseEvent,
    wordIndex: number,
    type: 'move' | 'resize-left' | 'resize-right'
  ) => {
    const rect = containerRef.current?.getBoundingClientRect()
    if (!rect) return

    const word = words[wordIndex]
    if (word.start_time === null || word.end_time === null) return

    const initialX = e.clientX - rect.left
    const initialTime = (initialX / rect.width) * viewDuration

    setDragState({
      wordIndex,
      type,
      initialX,
      initialTime,
      word,
    })
  }

  const handleMouseMove = (e: React.MouseEvent) => {
    if (!dragState || !containerRef.current) return

    const rect = containerRef.current.getBoundingClientRect()
    const x = e.clientX - rect.left
    const width = rect.width

    const currentWord = words[dragState.wordIndex]
    if (
      currentWord.start_time === null ||
      currentWord.end_time === null ||
      dragState.word.start_time === null ||
      dragState.word.end_time === null
    )
      return

    if (dragState.type === 'resize-right') {
      const initialWordDuration = dragState.word.end_time - dragState.word.start_time
      const initialWordWidth = (initialWordDuration / viewDuration) * width
      const pixelDelta = x - dragState.initialX
      const percentageMoved = pixelDelta / initialWordWidth
      const timeDelta = initialWordDuration * percentageMoved

      const proposedEnd = Math.max(
        currentWord.start_time + MIN_DURATION,
        dragState.word.end_time + timeDelta
      )

      if (checkCollision(currentWord.start_time, proposedEnd, dragState.wordIndex, true)) return

      onWordUpdate(dragState.wordIndex, {
        start_time: currentWord.start_time,
        end_time: proposedEnd,
      })
    } else if (dragState.type === 'resize-left') {
      const initialWordDuration = dragState.word.end_time - dragState.word.start_time
      const initialWordWidth = (initialWordDuration / viewDuration) * width
      const pixelDelta = x - dragState.initialX
      const percentageMoved = pixelDelta / initialWordWidth
      const timeDelta = initialWordDuration * percentageMoved

      const proposedStart = Math.min(
        currentWord.end_time - MIN_DURATION,
        dragState.word.start_time + timeDelta
      )

      if (checkCollision(proposedStart, currentWord.end_time, dragState.wordIndex, true)) return

      onWordUpdate(dragState.wordIndex, {
        start_time: proposedStart,
        end_time: currentWord.end_time,
      })
    } else if (dragState.type === 'move') {
      const pixelsPerSecond = width / viewDuration
      const pixelDelta = x - dragState.initialX
      const timeDelta = pixelDelta / pixelsPerSecond

      const wordDuration = currentWord.end_time - currentWord.start_time
      const proposedStart = dragState.word.start_time + timeDelta
      const proposedEnd = proposedStart + wordDuration

      // Allow dragging a little outside the segment (into the padded view) so the first/last
      // word can extend the segment; updateSegment recomputes the segment bounds from the words.
      if (proposedStart < viewStart || proposedEnd > viewEnd) return
      if (checkCollision(proposedStart, proposedEnd, dragState.wordIndex, false)) return

      onWordUpdate(dragState.wordIndex, {
        start_time: proposedStart,
        end_time: proposedEnd,
      })
    }
  }

  const handleMouseUp = () => {
    setDragState(null)
  }

  const handleContextMenu = (e: React.MouseEvent, wordIndex: number) => {
    e.preventDefault()
    e.stopPropagation()

    const word = words[wordIndex]
    if (word.start_time === null || word.end_time === null) return

    if (onUnsyncWord) {
      onUnsyncWord(wordIndex)
    }
  }

  const isWordHighlighted = (word: Word): boolean => {
    if (!currentTime || word.start_time === null || word.end_time === null) return false
    return currentTime >= word.start_time && currentTime <= word.end_time
  }

  const handleTimelineClick = (e: React.MouseEvent) => {
    const rect = containerRef.current?.getBoundingClientRect()
    if (!rect || !onPlaySegment) return
    // A not-yet-laid-out container has zero width, making the ratio non-finite;
    // bail so we never hand a NaN/Infinity time to the audio element.
    if (rect.width <= 0) return

    const x = e.clientX - rect.left
    const clickedPosition = (x / rect.width) * viewDuration + viewStart
    if (!Number.isFinite(clickedPosition)) return

    onPlaySegment(clickedPosition)
  }

  return (
    <div
      ref={containerRef}
      className="relative bg-card rounded border border-border"
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
    >
      {/* Out-of-segment padding: greyed bands on each side of the real segment. These sit above
          the waveform but below the word bars and are click-through so playback scrubbing still
          works. Boundary lines mark exactly where the current segment starts/ends. */}
      <div
        className="absolute inset-y-0 left-0 bg-muted-foreground/10 pointer-events-none z-[5] border-r border-dashed border-muted-foreground/40"
        style={{ width: `${timeToPosition(startTime)}%` }}
      />
      <div
        className="absolute inset-y-0 right-0 bg-muted-foreground/10 pointer-events-none z-[5] border-l border-dashed border-muted-foreground/40"
        style={{ width: `${100 - timeToPosition(endTime)}%` }}
      />

      {/* Timeline ruler */}
      <div
        className="h-10 border-b border-border cursor-pointer"
        onClick={handleTimelineClick}
      >
        {generateTimelineMarks()}
      </div>

      {/* Playback cursor — visible across the padded view (incl. lead-in/out) */}
      {showPlaybackIndicator && currentTime >= viewStart && currentTime <= viewEnd && (
        <div
          className="absolute top-0 w-0.5 h-full bg-destructive pointer-events-none transition-[left] duration-100 z-10"
          style={{ left: `${timeToPosition(currentTime)}%` }}
        />
      )}

      {/* Word blocks */}
      <div className="relative h-[30px]">
        {/* Neighbouring segments' words that fall in the padded view — greyed, read-only context. */}
        {(contextWords ?? []).map((word, index) => {
          if (word.start_time === null || word.end_time === null) return null
          if (word.end_time < viewStart || word.start_time > viewEnd) return null

          const leftPosition = timeToPosition(word.start_time)
          const rightPosition = timeToPosition(word.end_time)
          const width = rightPosition - leftPosition

          return (
            <div
              key={`ctx-${word.id ?? index}`}
              className={cn(
                'absolute bg-muted-foreground/25 text-muted-foreground rounded px-2 py-1',
                'select-none flex items-center text-sm font-sans pointer-events-none',
                'border border-dashed border-muted-foreground/40 overflow-hidden whitespace-nowrap'
              )}
              style={{
                left: `${leftPosition}%`,
                width: `${width}%`,
                maxWidth: `calc(${100 - leftPosition}%)`,
              }}
              title={`${word.text} (neighbouring segment)`}
            >
              {word.text}
            </div>
          )
        })}
        {words.map((word, index) => {
          if (word.start_time === null || word.end_time === null) return null

          const leftPosition = timeToPosition(word.start_time)
          const rightPosition = timeToPosition(word.end_time)
          const width = rightPosition - leftPosition

          return (
            <div
              key={index}
              className={cn(
                'absolute bg-primary rounded text-primary-foreground px-2 py-1',
                'cursor-move select-none flex items-center text-sm font-sans transition-colors',
                isWordHighlighted(word) && 'bg-purple-500 dark:bg-purple-600'
              )}
              style={{
                left: `${leftPosition}%`,
                width: `${width}%`,
                maxWidth: `calc(${100 - leftPosition}%)`,
              }}
              onMouseDown={(e) => {
                e.stopPropagation()
                handleMouseDown(e, index, 'move')
              }}
              onContextMenu={(e) => handleContextMenu(e, index)}
            >
              {/* Left resize handle */}
              <div
                className="absolute top-0 left-0 w-2.5 h-full cursor-col-resize hover:bg-primary-foreground/20 rounded-l"
                onMouseDown={(e) => {
                  e.stopPropagation()
                  handleMouseDown(e, index, 'resize-left')
                }}
              />
              {word.text}
              {/* Right resize handle */}
              <div
                className="absolute top-0 right-0 w-2.5 h-full cursor-col-resize hover:bg-primary-foreground/20 rounded-r"
                onMouseDown={(e) => {
                  e.stopPropagation()
                  handleMouseDown(e, index, 'resize-right')
                }}
              />
            </div>
          )
        })}
      </div>

      <VocalsAudioDataLoaderContext.Consumer>
        {({ audioData: vocalsAudioData }) => (
          vocalsAudioData && <WaveformVisualizer
            startTime={viewStart}
            endTime={viewEnd}
            fadeBeforeTime={startTime}
            fadeAfterTime={endTime}
            audioData={vocalsAudioData}
            className="w-[100%] h-[35px]"
          />
        )}
      </VocalsAudioDataLoaderContext.Consumer>
    </div>
  )
}
