'use client'

import { useRef, useEffect, useCallback, useState, memo } from 'react'
import { Button } from '@/components/ui/button'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { Word, LyricsSegment } from '@/lib/lyrics-review/types'

interface TimelineCanvasProps {
  words: Word[]
  segments: LyricsSegment[]
  visibleStartTime: number
  visibleEndTime: number
  currentTime: number
  selectedWordIds: Set<string>
  onWordClick: (wordId: string, event: React.MouseEvent) => void
  onBackgroundClick: () => void
  onTimeBarClick: (time: number) => void
  onSelectionComplete: (wordIds: string[]) => void
  onWordTimingChange: (wordId: string, newStartTime: number, newEndTime: number) => void
  onWordsMove: (updates: Array<{ wordId: string; newStartTime: number; newEndTime: number }>) => void
  syncWordIndex: number
  isManualSyncing: boolean
  onScrollChange: (newStartTime: number) => void
  audioDuration: number
  zoomSeconds: number
  height?: number
  isDarkMode?: boolean
}

// Constants for rendering
const TIME_BAR_HEIGHT = 28
const WORD_BLOCK_HEIGHT = 24
const WORD_LEVEL_SPACING = 50
const CANVAS_PADDING = 8
const TEXT_ABOVE_BLOCK = 14
const RESIZE_HANDLE_SIZE = 8
const RESIZE_HANDLE_HITAREA = 12

// Theme-aware colors
const getThemeColors = (isDarkMode: boolean) => ({
  playhead: '#f97316', // orange-500
  wordBlock: isDarkMode ? '#dc2626' : '#ef4444',
  wordBlockSelected: isDarkMode ? '#b91c1c' : '#dc2626',
  wordBlockCurrent: isDarkMode ? '#ef4444' : '#f87171',
  wordTextCurrent: isDarkMode ? '#fca5a5' : '#991b1b',
  wordText: isDarkMode ? '#f8fafc' : '#1e293b',
  upcomingWordBg: isDarkMode ? '#2a2a2a' : '#e5e7eb',
  upcomingWordText: isDarkMode ? '#e5e5e5' : '#374151',
  timeBarBg: isDarkMode ? '#1a1a1a' : '#f3f4f6',
  timeBarText: isDarkMode ? '#888888' : '#6b7280',
  timelineBg: isDarkMode ? '#0f0f0f' : '#ffffff',
  gridLine: isDarkMode ? '#64748b' : '#94a3b8',
  borderLine: isDarkMode ? '#2a2a2a' : '#cbd5e1',
  handleStroke: isDarkMode ? '#0f0f0f' : '#ffffff',
  playheadShadow: isDarkMode ? 'rgba(0,0,0,0.6)' : 'rgba(0,0,0,0.3)',
})

type DragMode = 'none' | 'selection' | 'resize' | 'move'

function buildWordToSegmentMap(segments: LyricsSegment[]): Map<string, number> {
  const map = new Map<string, number>()
  segments.forEach((segment, idx) => {
    segment.words.forEach((word) => {
      map.set(word.id, idx)
    })
  })
  return map
}

function calculateWordLevels(words: Word[], segments: LyricsSegment[]): Map<string, number> {
  const levels = new Map<string, number>()
  const wordToSegment = buildWordToSegmentMap(segments)

  const segmentsWithTiming = segments
    .map((segment, idx) => {
      const timedWords = segment.words.filter((w) => w.start_time !== null)
      const minStart =
        timedWords.length > 0 ? Math.min(...timedWords.map((w) => w.start_time!)) : Infinity
      return { idx, minStart }
    })
    .filter((s) => s.minStart !== Infinity)
    .sort((a, b) => a.minStart - b.minStart)

  const segmentLevels = new Map<number, number>()
  segmentsWithTiming.forEach(({ idx }, orderIndex) => {
    segmentLevels.set(idx, orderIndex % 2)
  })

  for (const word of words) {
    const segmentIdx = wordToSegment.get(word.id)
    if (segmentIdx !== undefined && segmentLevels.has(segmentIdx)) {
      levels.set(word.id, segmentLevels.get(segmentIdx)!)
    } else {
      levels.set(word.id, 0)
    }
  }

  return levels
}

function formatTime(seconds: number): string {
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
}

const TimelineCanvas = memo(function TimelineCanvas({
  words,
  segments,
  visibleStartTime,
  visibleEndTime,
  currentTime,
  selectedWordIds,
  onWordClick,
  onBackgroundClick,
  onTimeBarClick,
  onSelectionComplete,
  onWordTimingChange,
  onWordsMove,
  syncWordIndex,
  isManualSyncing,
  onScrollChange,
  audioDuration,
  zoomSeconds,
  height = 200,
  isDarkMode = true,
}: TimelineCanvasProps) {
  const colors = getThemeColors(isDarkMode)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [canvasWidth, setCanvasWidth] = useState(800)
  const animationFrameRef = useRef<number | undefined>(undefined)
  const wordLevelsRef = useRef<Map<string, number>>(new Map())

  // Drag state
  const [dragMode, setDragMode] = useState<DragMode>('none')
  const dragStartRef = useRef<{ x: number; y: number; time: number } | null>(null)
  const dragWordIdRef = useRef<string | null>(null)
  const dragOriginalTimesRef = useRef<Map<string, { start: number; end: number }>>(new Map())

  // Selection rectangle
  const [selectionRect, setSelectionRect] = useState<{
    startX: number
    startY: number
    endX: number
    endY: number
  } | null>(null)

  // Hover state
  const [hoveredWordId, setHoveredWordId] = useState<string | null>(null)
  const [cursorStyle, setCursorStyle] = useState<string>('default')

  // Update canvas width on resize
  useEffect(() => {
    const updateWidth = () => {
      if (containerRef.current) {
        setCanvasWidth(containerRef.current.clientWidth)
      }
    }

    updateWidth()
    const resizeObserver = new ResizeObserver(updateWidth)
    if (containerRef.current) {
      resizeObserver.observe(containerRef.current)
    }

    return () => resizeObserver.disconnect()
  }, [])

  // Calculate word levels when words or segments change
  useEffect(() => {
    wordLevelsRef.current = calculateWordLevels(words, segments)
  }, [words, segments])

  // Convert time to x position
  const timeToX = useCallback(
    (time: number): number => {
      const duration = visibleEndTime - visibleStartTime
      if (duration <= 0) return 0
      return (
        CANVAS_PADDING +
        ((time - visibleStartTime) / duration) * (canvasWidth - CANVAS_PADDING * 2)
      )
    },
    [visibleStartTime, visibleEndTime, canvasWidth]
  )

  // Convert x position to time
  const xToTime = useCallback(
    (x: number): number => {
      const duration = visibleEndTime - visibleStartTime
      return (
        visibleStartTime + ((x - CANVAS_PADDING) / (canvasWidth - CANVAS_PADDING * 2)) * duration
      )
    },
    [visibleStartTime, visibleEndTime, canvasWidth]
  )

  // Get word bounds
  const getWordBounds = useCallback(
    (word: Word) => {
      if (word.start_time === null || word.end_time === null) return null

      const level = wordLevelsRef.current.get(word.id) || 0
      const startX = timeToX(word.start_time)
      const endX = timeToX(word.end_time)
      const blockWidth = Math.max(endX - startX, 4)
      const y = TIME_BAR_HEIGHT + CANVAS_PADDING + TEXT_ABOVE_BLOCK + level * WORD_LEVEL_SPACING

      return { startX, endX, blockWidth, y, level }
    },
    [timeToX]
  )

  // Check if position is near resize handle
  const isNearResizeHandlePos = useCallback(
    (word: Word, x: number, y: number): boolean => {
      const bounds = getWordBounds(word)
      if (!bounds) return false

      const handleX = bounds.startX + bounds.blockWidth - RESIZE_HANDLE_SIZE / 2
      const handleY = bounds.y + WORD_BLOCK_HEIGHT / 2

      return (
        Math.abs(x - handleX) < RESIZE_HANDLE_HITAREA / 2 &&
        Math.abs(y - handleY) < RESIZE_HANDLE_HITAREA / 2
      )
    },
    [getWordBounds]
  )

  // Find word at position
  const findWordAtPosition = useCallback(
    (x: number, y: number): Word | null => {
      for (const word of words) {
        const bounds = getWordBounds(word)
        if (!bounds) continue

        if (
          x >= bounds.startX &&
          x <= bounds.startX + bounds.blockWidth &&
          y >= bounds.y &&
          y <= bounds.y + WORD_BLOCK_HEIGHT
        ) {
          return word
        }
      }
      return null
    },
    [words, getWordBounds]
  )

  // Find words in selection rectangle
  const findWordsInRect = useCallback(
    (rect: { startX: number; startY: number; endX: number; endY: number }): string[] => {
      const rectLeft = Math.min(rect.startX, rect.endX)
      const rectRight = Math.max(rect.startX, rect.endX)
      const rectTop = Math.min(rect.startY, rect.endY)
      const rectBottom = Math.max(rect.startY, rect.endY)

      const selectedIds: string[] = []

      for (const word of words) {
        const bounds = getWordBounds(word)
        if (!bounds) continue

        if (
          bounds.startX + bounds.blockWidth >= rectLeft &&
          bounds.startX <= rectRight &&
          bounds.y + WORD_BLOCK_HEIGHT >= rectTop &&
          bounds.y <= rectBottom
        ) {
          selectedIds.push(word.id)
        }
      }

      return selectedIds
    },
    [words, getWordBounds]
  )

  // Draw the timeline
  const draw = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const dpr = window.devicePixelRatio || 1
    canvas.width = canvasWidth * dpr
    canvas.height = height * dpr
    ctx.scale(dpr, dpr)

    // Clear canvas
    ctx.fillStyle = colors.timelineBg
    ctx.fillRect(0, 0, canvasWidth, height)

    // Draw time bar background
    ctx.fillStyle = colors.timeBarBg
    ctx.fillRect(0, 0, canvasWidth, TIME_BAR_HEIGHT)

    // Draw time markers
    const duration = visibleEndTime - visibleStartTime
    const secondsPerTick = duration > 15 ? 2 : duration > 8 ? 1 : 0.5
    const startSecond = Math.ceil(visibleStartTime / secondsPerTick) * secondsPerTick

    ctx.fillStyle = colors.timeBarText
    ctx.font = '11px system-ui, -apple-system, sans-serif'
    ctx.textAlign = 'center'

    for (let t = startSecond; t <= visibleEndTime; t += secondsPerTick) {
      const x = timeToX(t)

      ctx.beginPath()
      ctx.strokeStyle = colors.gridLine
      ctx.lineWidth = 1
      ctx.moveTo(x, TIME_BAR_HEIGHT - 6)
      ctx.lineTo(x, TIME_BAR_HEIGHT)
      ctx.stroke()

      if (t % 1 === 0) {
        ctx.fillText(formatTime(t), x, TIME_BAR_HEIGHT - 10)
      }
    }

    ctx.beginPath()
    ctx.strokeStyle = colors.borderLine
    ctx.lineWidth = 1
    ctx.moveTo(0, TIME_BAR_HEIGHT)
    ctx.lineTo(canvasWidth, TIME_BAR_HEIGHT)
    ctx.stroke()

    const wordToSegment = buildWordToSegmentMap(segments)
    const syncedWords = words.filter((w) => w.start_time !== null && w.end_time !== null)

    const currentWordId =
      syncedWords.find((w) => currentTime >= w.start_time! && currentTime <= w.end_time!)?.id ||
      null

    // First pass: draw all blocks
    for (const word of syncedWords) {
      const bounds = getWordBounds(word)
      if (!bounds) continue

      const isSelected = selectedWordIds.has(word.id)
      const isCurrent = word.id === currentWordId
      const isHovered = word.id === hoveredWordId

      // Draw word block background
      if (isSelected) {
        ctx.fillStyle = colors.wordBlockSelected
      } else if (isCurrent) {
        ctx.fillStyle = colors.wordBlockCurrent
      } else {
        ctx.fillStyle = colors.wordBlock
      }
      ctx.fillRect(bounds.startX, bounds.y, bounds.blockWidth, WORD_BLOCK_HEIGHT)

      // Draw selection border
      if (isSelected) {
        ctx.strokeStyle = colors.playhead
        ctx.lineWidth = 2
        ctx.strokeRect(bounds.startX, bounds.y, bounds.blockWidth, WORD_BLOCK_HEIGHT)

        // Draw resize handle
        if (isHovered || selectedWordIds.size === 1) {
          const handleX = bounds.startX + bounds.blockWidth - RESIZE_HANDLE_SIZE / 2
          const handleY = bounds.y + WORD_BLOCK_HEIGHT / 2

          ctx.beginPath()
          ctx.fillStyle = colors.playhead
          ctx.arc(handleX, handleY, RESIZE_HANDLE_SIZE / 2, 0, Math.PI * 2)
          ctx.fill()
          ctx.strokeStyle = colors.handleStroke
          ctx.lineWidth = 1
          ctx.stroke()
        }
      }
    }

    // Second pass: draw text
    const wordsBySegment = new Map<number, Word[]>()
    for (const word of syncedWords) {
      const segIdx = wordToSegment.get(word.id)
      if (segIdx !== undefined) {
        if (!wordsBySegment.has(segIdx)) {
          wordsBySegment.set(segIdx, [])
        }
        wordsBySegment.get(segIdx)!.push(word)
      }
    }

    ctx.font = '11px system-ui, -apple-system, sans-serif'
    ctx.textAlign = 'left'

    for (const [, segmentWords] of wordsBySegment) {
      const sortedWords = [...segmentWords].sort(
        (a, b) => (a.start_time || 0) - (b.start_time || 0)
      )

      if (sortedWords.length === 0) continue

      const level = wordLevelsRef.current.get(sortedWords[0].id) || 0
      const textY =
        TIME_BAR_HEIGHT + CANVAS_PADDING + TEXT_ABOVE_BLOCK + level * WORD_LEVEL_SPACING - 3

      let rightmostTextEnd = -Infinity

      for (const word of sortedWords) {
        const blockStartX = timeToX(word.start_time!)
        const textWidth = ctx.measureText(word.text).width
        const textStartX = Math.max(blockStartX, rightmostTextEnd + 3)

        if (textStartX < canvasWidth - 10) {
          const isCurrent = word.id === currentWordId
          ctx.fillStyle = isCurrent ? colors.wordTextCurrent : colors.wordText
          ctx.fillText(word.text, textStartX, textY)
          rightmostTextEnd = textStartX + textWidth
        }
      }
    }

    // Draw upcoming words during sync
    if (isManualSyncing && syncWordIndex >= 0) {
      const upcomingWords = words.slice(syncWordIndex).filter((w) => w.start_time === null)
      const playheadX = timeToX(currentTime)
      let offsetX = playheadX + 10

      ctx.font = '11px system-ui, -apple-system, sans-serif'

      for (let i = 0; i < Math.min(upcomingWords.length, 12); i++) {
        const word = upcomingWords[i]
        const textWidth = ctx.measureText(word.text).width + 10

        ctx.fillStyle = colors.upcomingWordBg
        ctx.fillRect(
          offsetX,
          TIME_BAR_HEIGHT + CANVAS_PADDING + WORD_LEVEL_SPACING + 60,
          textWidth,
          20
        )

        ctx.fillStyle = colors.upcomingWordText
        ctx.textAlign = 'left'
        ctx.fillText(
          word.text,
          offsetX + 5,
          TIME_BAR_HEIGHT + CANVAS_PADDING + WORD_LEVEL_SPACING + 74
        )

        offsetX += textWidth + 3
        if (offsetX > canvasWidth - 20) break
      }
    }

    // Draw playhead
    if (currentTime >= visibleStartTime && currentTime <= visibleEndTime) {
      const playheadX = timeToX(currentTime)

      ctx.beginPath()
      ctx.fillStyle = colors.playhead
      ctx.strokeStyle = colors.handleStroke
      ctx.lineWidth = 1
      ctx.moveTo(playheadX - 6, 2)
      ctx.lineTo(playheadX + 6, 2)
      ctx.lineTo(playheadX, TIME_BAR_HEIGHT - 4)
      ctx.closePath()
      ctx.fill()
      ctx.stroke()

      ctx.beginPath()
      ctx.strokeStyle = colors.playhead
      ctx.lineWidth = 2
      ctx.moveTo(playheadX, TIME_BAR_HEIGHT)
      ctx.lineTo(playheadX, height)
      ctx.stroke()

      ctx.beginPath()
      ctx.strokeStyle = colors.playheadShadow
      ctx.lineWidth = 1
      ctx.moveTo(playheadX + 1, TIME_BAR_HEIGHT)
      ctx.lineTo(playheadX + 1, height)
      ctx.stroke()
    }

    // Draw selection rectangle
    if (selectionRect) {
      ctx.fillStyle = 'rgba(25, 118, 210, 0.2)'
      ctx.strokeStyle = 'rgba(25, 118, 210, 0.8)'
      ctx.lineWidth = 1

      const rectX = Math.min(selectionRect.startX, selectionRect.endX)
      const rectY = Math.min(selectionRect.startY, selectionRect.endY)
      const rectW = Math.abs(selectionRect.endX - selectionRect.startX)
      const rectH = Math.abs(selectionRect.endY - selectionRect.startY)

      ctx.fillRect(rectX, rectY, rectW, rectH)
      ctx.strokeRect(rectX, rectY, rectW, rectH)
    }
  }, [
    canvasWidth,
    height,
    visibleStartTime,
    visibleEndTime,
    currentTime,
    words,
    segments,
    selectedWordIds,
    selectionRect,
    hoveredWordId,
    syncWordIndex,
    isManualSyncing,
    timeToX,
    getWordBounds,
    colors,
  ])

  // Animation frame
  useEffect(() => {
    const animate = () => {
      draw()
      animationFrameRef.current = requestAnimationFrame(animate)
    }
    animate()

    return () => {
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current)
      }
    }
  }, [draw])

  // Mouse handlers
  const handleMouseDown = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      const rect = canvasRef.current?.getBoundingClientRect()
      if (!rect) return

      const x = e.clientX - rect.left
      const y = e.clientY - rect.top
      const time = xToTime(x)

      // Time bar click
      if (y < TIME_BAR_HEIGHT) {
        onTimeBarClick(Math.max(0, time))
        return
      }

      const clickedWord = findWordAtPosition(x, y)

      if (clickedWord && selectedWordIds.has(clickedWord.id)) {
        // Check if clicking on resize handle
        if (isNearResizeHandlePos(clickedWord, x, y)) {
          setDragMode('resize')
          dragStartRef.current = { x, y, time }
          dragWordIdRef.current = clickedWord.id
          dragOriginalTimesRef.current = new Map([
            [
              clickedWord.id,
              {
                start: clickedWord.start_time!,
                end: clickedWord.end_time!,
              },
            ],
          ])
          return
        }

        // Start move
        setDragMode('move')
        dragStartRef.current = { x, y, time }
        dragWordIdRef.current = clickedWord.id

        const originalTimes = new Map<string, { start: number; end: number }>()
        for (const wordId of selectedWordIds) {
          const word = words.find((w) => w.id === wordId)
          if (word && word.start_time !== null && word.end_time !== null) {
            originalTimes.set(wordId, { start: word.start_time, end: word.end_time })
          }
        }
        dragOriginalTimesRef.current = originalTimes
        return
      }

      if (clickedWord) {
        onWordClick(clickedWord.id, e)
        return
      }

      // Background - start selection
      setDragMode('selection')
      dragStartRef.current = { x, y, time }
      setSelectionRect({ startX: x, startY: y, endX: x, endY: y })
    },
    [xToTime, onTimeBarClick, findWordAtPosition, selectedWordIds, isNearResizeHandlePos, onWordClick, words]
  )

  const handleMouseMove = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      const rect = canvasRef.current?.getBoundingClientRect()
      if (!rect) return

      const x = e.clientX - rect.left
      const y = e.clientY - rect.top
      const time = xToTime(x)

      // Update hover state and cursor
      if (dragMode === 'none') {
        const hoveredWord = findWordAtPosition(x, y)
        setHoveredWordId(hoveredWord?.id || null)

        if (hoveredWord && selectedWordIds.has(hoveredWord.id)) {
          const nearHandle = isNearResizeHandlePos(hoveredWord, x, y)
          setCursorStyle(nearHandle ? 'ew-resize' : 'grab')
        } else if (hoveredWord) {
          setCursorStyle('pointer')
        } else if (y < TIME_BAR_HEIGHT) {
          setCursorStyle('pointer')
        } else {
          setCursorStyle('default')
        }
      }

      if (!dragStartRef.current) return

      if (dragMode === 'selection') {
        setSelectionRect({
          startX: dragStartRef.current.x,
          startY: dragStartRef.current.y,
          endX: x,
          endY: y,
        })
      } else if (dragMode === 'resize' && dragWordIdRef.current) {
        const originalTimes = dragOriginalTimesRef.current.get(dragWordIdRef.current)
        if (originalTimes) {
          const deltaTime = time - dragStartRef.current.time
          const newEndTime = Math.max(originalTimes.start + 0.05, originalTimes.end + deltaTime)
          onWordTimingChange(dragWordIdRef.current, originalTimes.start, newEndTime)
        }
        setCursorStyle('ew-resize')
      } else if (dragMode === 'move') {
        const deltaTime = time - dragStartRef.current.time
        const updates: Array<{ wordId: string; newStartTime: number; newEndTime: number }> = []

        for (const [wordId, originalTimes] of dragOriginalTimesRef.current) {
          const newStartTime = Math.max(0, originalTimes.start + deltaTime)
          const newEndTime = Math.max(newStartTime + 0.05, originalTimes.end + deltaTime)
          updates.push({ wordId, newStartTime, newEndTime })
        }

        if (updates.length > 0) {
          onWordsMove(updates)
        }
        setCursorStyle('grabbing')
      }
    },
    [dragMode, xToTime, findWordAtPosition, selectedWordIds, isNearResizeHandlePos, onWordTimingChange, onWordsMove]
  )

  const handleMouseUp = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      const rect = canvasRef.current?.getBoundingClientRect()

      if (dragMode === 'selection' && dragStartRef.current && rect) {
        const endX = e.clientX - rect.left
        const endY = e.clientY - rect.top

        const dragDistance = Math.sqrt(
          Math.pow(endX - dragStartRef.current.x, 2) + Math.pow(endY - dragStartRef.current.y, 2)
        )

        if (dragDistance < 5) {
          onBackgroundClick()
        } else {
          const finalRect = {
            startX: dragStartRef.current.x,
            startY: dragStartRef.current.y,
            endX,
            endY,
          }
          const selectedIds = findWordsInRect(finalRect)
          if (selectedIds.length > 0) {
            onSelectionComplete(selectedIds)
          }
        }
      }

      // Reset drag state
      setDragMode('none')
      dragStartRef.current = null
      dragWordIdRef.current = null
      dragOriginalTimesRef.current = new Map()
      setSelectionRect(null)
      setCursorStyle('default')
    },
    [dragMode, onBackgroundClick, findWordsInRect, onSelectionComplete]
  )

  // Wheel handler
  const handleWheel = useCallback(
    (e: React.WheelEvent<HTMLCanvasElement>) => {
      const delta = e.deltaX !== 0 ? e.deltaX : e.deltaY
      const scrollAmount = (delta / 100) * (zoomSeconds / 4)
      const newStart = Math.max(
        0,
        Math.min(audioDuration - zoomSeconds, visibleStartTime + scrollAmount)
      )

      if (newStart !== visibleStartTime) {
        onScrollChange(newStart)
      }
    },
    [visibleStartTime, zoomSeconds, audioDuration, onScrollChange]
  )

  const handleScrollLeft = useCallback(() => {
    const newStart = Math.max(0, visibleStartTime - zoomSeconds * 0.25)
    onScrollChange(newStart)
  }, [visibleStartTime, zoomSeconds, onScrollChange])

  const handleScrollRight = useCallback(() => {
    const newStart = Math.min(audioDuration - zoomSeconds, visibleStartTime + zoomSeconds * 0.25)
    onScrollChange(Math.max(0, newStart))
  }, [visibleStartTime, zoomSeconds, audioDuration, onScrollChange])

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-2">
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8"
          onClick={handleScrollLeft}
          disabled={visibleStartTime <= 0}
        >
          <ChevronLeft className="h-4 w-4" />
        </Button>

        <div
          ref={containerRef}
          className="flex-1 rounded overflow-hidden"
          style={{ height, cursor: cursorStyle }}
        >
          <canvas
            ref={canvasRef}
            style={{
              width: '100%',
              height: '100%',
              display: 'block',
              cursor: cursorStyle,
            }}
            onMouseDown={handleMouseDown}
            onMouseMove={handleMouseMove}
            onMouseUp={handleMouseUp}
            onMouseLeave={handleMouseUp}
            onWheel={handleWheel}
          />
        </div>

        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8"
          onClick={handleScrollRight}
          disabled={visibleStartTime >= audioDuration - zoomSeconds}
        >
          <ChevronRight className="h-4 w-4" />
        </Button>
      </div>
    </div>
  )
})

export default TimelineCanvas
