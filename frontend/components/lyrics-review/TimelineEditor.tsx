'use client'

import { useRef, useState } from 'react'
import { Word } from '@/lib/lyrics-review/types'
import { cn } from '@/lib/utils'

interface TimelineEditorProps {
  words: Word[]
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

  const checkCollision = (
    proposedStart: number,
    proposedEnd: number,
    currentIndex: number,
    isResize: boolean
  ): boolean => {
    if (isResize) {
      if (currentIndex === words.length - 1) return false

      const nextWord = words[currentIndex + 1]
      if (!nextWord || nextWord.start_time === null) return false
      return proposedEnd > nextWord.start_time
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
    const duration = endTime - startTime
    const position = ((time - startTime) / duration) * 100
    return Math.max(0, Math.min(100, position))
  }

  const generateTimelineMarks = () => {
    const marks = []
    const startSecond = Math.floor(startTime)
    const endSecond = Math.ceil(endTime)

    for (let time = startSecond; time <= endSecond; time++) {
      if (time >= startTime && time <= endTime) {
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
    const initialTime = (initialX / rect.width) * (endTime - startTime)

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
      const initialWordWidth = (initialWordDuration / (endTime - startTime)) * width
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
      const initialWordWidth = (initialWordDuration / (endTime - startTime)) * width
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
      const pixelsPerSecond = width / (endTime - startTime)
      const pixelDelta = x - dragState.initialX
      const timeDelta = pixelDelta / pixelsPerSecond

      const wordDuration = currentWord.end_time - currentWord.start_time
      const proposedStart = dragState.word.start_time + timeDelta
      const proposedEnd = proposedStart + wordDuration

      if (proposedStart < startTime || proposedEnd > endTime) return
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

    const x = e.clientX - rect.left
    const clickedPosition = (x / rect.width) * (endTime - startTime) + startTime

    onPlaySegment(clickedPosition)
  }

  return (
    <div
      ref={containerRef}
      className="relative h-[75px] bg-card rounded my-2 px-2 border border-border"
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
    >
      {/* Timeline ruler */}
      <div
        className="absolute top-0 left-0 right-0 h-10 border-b border-border cursor-pointer"
        onClick={handleTimelineClick}
      >
        {generateTimelineMarks()}
      </div>

      {/* Playback cursor */}
      {showPlaybackIndicator && currentTime >= startTime && currentTime <= endTime && (
        <div
          className="absolute top-0 w-0.5 h-full bg-destructive pointer-events-none transition-[left] duration-100 z-10"
          style={{ left: `${timeToPosition(currentTime)}%` }}
        />
      )}

      {/* Word blocks */}
      {words.map((word, index) => {
        if (word.start_time === null || word.end_time === null) return null

        const leftPosition = timeToPosition(word.start_time)
        const rightPosition = timeToPosition(word.end_time)
        const width = rightPosition - leftPosition

        return (
          <div
            key={index}
            className={cn(
              'absolute h-[30px] top-10 bg-primary rounded text-primary-foreground px-2 py-1',
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
  )
}
