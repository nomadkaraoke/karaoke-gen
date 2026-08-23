'use client'

import { memo, useRef, useCallback } from 'react'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import { Hand } from 'lucide-react'
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
  currentTime?: number
  isManualSyncing: boolean
  syncWordIndex: number
  isSpacebarPressed: boolean
  onWordUpdate: (index: number, updates: Partial<Word>) => void
  onPlaySegment?: (time: number) => void
  isGlobal?: boolean
  onTapStart?: () => void
  onTapEnd?: () => void
}

export default function EditTimelineSection({
  words,
  startTime,
  endTime,
  currentTime,
  isManualSyncing,
  syncWordIndex,
  isSpacebarPressed,
  onWordUpdate,
  onPlaySegment,
  isGlobal = false,
  onTapStart,
  onTapEnd,
}: EditTimelineSectionProps) {
  const t = useTranslations('lyricsReview.editTimeline')
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
      {/* Timeline Editor — height hugs its content (ruler + word bars + waveform)
          instead of a fixed min-height, to avoid dead vertical space. */}
      <div>
        <TimelineEditor
          words={words}
          startTime={startTime}
          endTime={endTime}
          onWordUpdate={onWordUpdate}
          currentTime={currentTime}
          onPlaySegment={onPlaySegment}
        />
      </div>

      {/* Sync helper — only visible while manually syncing. The Tap To Sync
          toggle and the time-range readout now live in the modal header to
          reclaim the vertical space above the word list. */}
      {(currentWordInfo || (isMobile && isManualSyncing)) && (
        <div
          className={cn(
            'flex items-center gap-2',
            isMobile ? 'flex-col items-stretch mt-2 mb-3' : 'flex-row mt-0 mb-0'
          )}
        >
          {/* Current word info during sync */}
          {currentWordInfo && (
            <div className={cn('text-center', isMobile ? '' : 'text-left')}>
              <div className="text-sm">
                {t('wordProgress', {
                  index: currentWordInfo.index,
                  total: currentWordInfo.total,
                })}{' '}
                <strong>{currentWordInfo.text}</strong>
              </div>
              <div className="text-xs text-muted-foreground">
                {isSpacebarPressed
                  ? t('holdingRelease')
                  : isMobile
                    ? t('tapWhenStarts')
                    : t('pressSpacebarHint')}
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
      )}
    </>
  )
}
