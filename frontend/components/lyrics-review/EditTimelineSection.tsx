'use client'

import { memo, useRef, useCallback } from 'react'
import { Button } from '@/components/ui/button'
import { Play, XCircle, Hand } from 'lucide-react'
import { Word } from '@/lib/lyrics-review/types'
import TimelineEditor from './TimelineEditor'
import { cn } from '@/lib/utils'

// Separate TapButton component to properly track local press state
const TapButton = memo(function TapButton({
  isSpacebarPressed,
  onTapStart,
  onTapEnd,
}: {
  isSpacebarPressed: boolean
  onTapStart: () => void
  onTapEnd: () => void
}) {
  const isPressedRef = useRef(false)

  const handleTapStart = useCallback(() => {
    isPressedRef.current = true
    onTapStart()
  }, [onTapStart])

  const handleTapEnd = useCallback(() => {
    if (isPressedRef.current) {
      isPressedRef.current = false
      onTapEnd()
    }
  }, [onTapEnd])

  return (
    <Button
      variant={isSpacebarPressed ? 'secondary' : 'default'}
      onTouchStart={(e) => {
        e.preventDefault()
        handleTapStart()
      }}
      onTouchEnd={(e) => {
        e.preventDefault()
        handleTapEnd()
      }}
      onMouseDown={handleTapStart}
      onMouseUp={handleTapEnd}
      onMouseLeave={handleTapEnd}
      className={cn(
        'py-6 text-lg font-bold w-full min-h-[56px] select-none touch-manipulation',
        isSpacebarPressed && 'bg-secondary'
      )}
    >
      <Hand className="h-5 w-5 mr-2" />
      {isSpacebarPressed ? 'HOLD...' : 'TAP'}
    </Button>
  )
})

interface EditTimelineSectionProps {
  words: Word[]
  startTime: number
  endTime: number
  originalStartTime: number | null
  originalEndTime: number | null
  currentStartTime: number | null
  currentEndTime: number | null
  currentTime?: number
  isManualSyncing: boolean
  syncWordIndex: number
  isSpacebarPressed: boolean
  onWordUpdate: (index: number, updates: Partial<Word>) => void
  onPlaySegment?: (time: number) => void
  startManualSync: () => void
  isGlobal?: boolean
  onTapStart?: () => void
  onTapEnd?: () => void
}

export default function EditTimelineSection({
  words,
  startTime,
  endTime,
  originalStartTime,
  originalEndTime,
  currentStartTime,
  currentEndTime,
  currentTime,
  isManualSyncing,
  syncWordIndex,
  isSpacebarPressed,
  onWordUpdate,
  onPlaySegment,
  startManualSync,
  isGlobal = false,
  onTapStart,
  onTapEnd,
}: EditTimelineSectionProps) {
  // Simple mobile detection
  const isMobile = typeof window !== 'undefined' && window.innerWidth < 640

  // Get current word info for display during manual sync
  const currentWordInfo =
    isManualSyncing && syncWordIndex >= 0 && syncWordIndex < words.length
      ? {
          index: syncWordIndex + 1,
          total: words.length,
          text: words[syncWordIndex]?.text || '',
        }
      : null

  return (
    <>
      {/* Timeline Editor */}
      <div className={cn('mb-0', isMobile ? 'h-20' : 'h-[120px]')}>
        <TimelineEditor
          words={words}
          startTime={startTime}
          endTime={endTime}
          onWordUpdate={onWordUpdate}
          currentTime={currentTime}
          onPlaySegment={onPlaySegment}
        />
      </div>

      {/* Time range info and controls */}
      <div
        className={cn(
          'flex items-center justify-between gap-2',
          isMobile ? 'flex-col items-stretch mt-2 mb-3' : 'flex-row mt-0 mb-0'
        )}
      >
        {/* Time range info - hidden on mobile to save space */}
        {!isMobile && (
          <div className="text-xs text-muted-foreground">
            <div>
              Original Time Range: {originalStartTime?.toFixed(2) ?? 'N/A'} -{' '}
              {originalEndTime?.toFixed(2) ?? 'N/A'}
            </div>
            <div>
              Current Time Range: {currentStartTime?.toFixed(2) ?? 'N/A'} -{' '}
              {currentEndTime?.toFixed(2) ?? 'N/A'}
            </div>
          </div>
        )}

        {/* Controls */}
        <div
          className={cn(
            'flex items-center gap-2',
            isMobile ? 'flex-col items-stretch' : 'flex-row'
          )}
        >
          {/* Tap To Sync button */}
          <Button
            variant={isManualSyncing ? 'outline' : 'default'}
            onClick={startManualSync}
            className={cn(
              isManualSyncing
                ? 'border-destructive text-destructive hover:bg-destructive/10'
                : 'bg-primary'
            )}
          >
            {isManualSyncing ? (
              <>
                <XCircle className="h-4 w-4 mr-1" />
                Cancel
              </>
            ) : (
              <>
                <Play className="h-4 w-4 mr-1" />
                Tap To Sync
              </>
            )}
          </Button>

          {/* Current word info during sync */}
          {currentWordInfo && (
            <div className={cn('text-center', isMobile ? '' : 'text-left')}>
              <div className="text-sm">
                Word {currentWordInfo.index} of {currentWordInfo.total}:{' '}
                <strong>{currentWordInfo.text}</strong>
              </div>
              <div className="text-xs text-muted-foreground">
                {isSpacebarPressed
                  ? 'Holding... Release when word ends'
                  : isMobile
                    ? 'Tap the button when word starts'
                    : 'Press spacebar when word starts (tap for short words, hold for long words)'}
              </div>
            </div>
          )}

          {/* Mobile TAP button for manual sync */}
          {isMobile && isManualSyncing && onTapStart && onTapEnd && (
            <TapButton
              isSpacebarPressed={isSpacebarPressed}
              onTapStart={onTapStart}
              onTapEnd={onTapEnd}
            />
          )}
        </div>
      </div>
    </>
  )
}
