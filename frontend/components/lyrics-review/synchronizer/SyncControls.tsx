'use client'

import { useTranslations } from 'next-intl'
import { memo } from 'react'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import {
  Play,
  Square,
  Pause,
  PlayCircle,
  Trash2,
  Edit3,
  CircleSlash,
  FileEdit,
  Pointer,
} from 'lucide-react'

interface SyncControlsProps {
  // Main buttons
  isManualSyncing: boolean
  isPaused: boolean
  onStartSync: () => void
  onPauseSync: () => void
  onResumeSync: () => void
  onClearSync: () => void
  onEditLyrics: () => void

  // Playback controls
  onPlay: () => void
  onStop: () => void
  isPlaying: boolean

  // Word action buttons
  hasSelectedWords: boolean
  selectedWordCount: number
  onUnsyncFromCursor: () => void
  onEditSelectedWord: () => void
  onDeleteSelected: () => void

  // Additional state
  canUnsyncFromCursor: boolean

  // Mobile tap support
  isMobile?: boolean
  onTapStart?: () => void
  onTapEnd?: () => void
  isTapping?: boolean
}

const SyncControls = memo(function SyncControls({
  isManualSyncing,
  isPaused,
  onStartSync,
  onPauseSync,
  onResumeSync,
  onClearSync,
  onEditLyrics,
  onPlay,
  onStop,
  isPlaying,
  hasSelectedWords,
  selectedWordCount,
  onUnsyncFromCursor,
  onEditSelectedWord,
  onDeleteSelected,
  canUnsyncFromCursor,
  isMobile = false,
  onTapStart,
  onTapEnd,
  isTapping = false,
}: SyncControlsProps) {
  const t = useTranslations('lyricsReview.synchronizer.controls')
  return (
    <div className="flex flex-col gap-2">
      {/* Row 1: Playback & Sync Mode Controls */}
      <div className="flex flex-wrap gap-2 items-center">
        {/* Playback controls */}
        <Button variant="outline" size="sm" onClick={onPlay} disabled={isPlaying}>
          <Play className="h-4 w-4 mr-1" />
          {t('play')}
        </Button>
        <Button variant="outline" size="sm" onClick={onStop} disabled={!isPlaying}>
          <Square className="h-4 w-4 mr-1" />
          {t('stop')}
        </Button>

        {!isMobile && <Separator orientation="vertical" className="h-6" />}

        {/* Sync Mode controls */}
        {isManualSyncing ? (
          <>
            <Button variant="destructive" size="sm" onClick={onStartSync}>
              <Square className="h-4 w-4 mr-1" />
              {t('stopSync')}
            </Button>
            <Button
              variant={isPaused ? 'default' : 'outline'}
              size="sm"
              onClick={isPaused ? onResumeSync : onPauseSync}
              className={isPaused ? 'bg-green-600 hover:bg-green-700' : ''}
            >
              {isPaused ? <Play className="h-4 w-4 mr-1" /> : <Pause className="h-4 w-4 mr-1" />}
              {isPaused ? t('resume') : t('pause')}
            </Button>
            {/* TAP button for mobile - no spacebar on mobile */}
            {isMobile && !isPaused && onTapStart && onTapEnd && (
              <Button
                variant={isTapping ? 'secondary' : 'default'}
                size="sm"
                onTouchStart={(e) => {
                  e.preventDefault()
                  onTapStart()
                }}
                onTouchEnd={(e) => {
                  e.preventDefault()
                  onTapEnd()
                }}
                onMouseDown={onTapStart}
                onMouseUp={onTapEnd}
                className={isTapping ? 'bg-yellow-500' : 'bg-green-600'}
              >
                <Pointer className="h-4 w-4 mr-1" />
                {isTapping ? t('release') : t('tap')}
              </Button>
            )}
          </>
        ) : (
          <Button size="sm" onClick={onStartSync}>
            <PlayCircle className="h-4 w-4 mr-1" />
            {t('startSync')}
          </Button>
        )}
      </div>

      {/* Row 2: Editing & Word Actions */}
      <div className="flex flex-wrap gap-2 items-center">
        <Button
          variant="outline"
          size="sm"
          onClick={onClearSync}
          disabled={isManualSyncing && !isPaused}
        >
          <Trash2 className="h-4 w-4 mr-1" />
          {t('clearSync')}
        </Button>

        <Button
          variant="outline"
          size="sm"
          onClick={onEditLyrics}
          disabled={isManualSyncing && !isPaused}
        >
          <FileEdit className="h-4 w-4 mr-1" />
          {t('editLyrics')}
        </Button>

        {!isMobile && <Separator orientation="vertical" className="h-6" />}

        <Button
          variant="outline"
          size="sm"
          onClick={onUnsyncFromCursor}
          disabled={!canUnsyncFromCursor || (isManualSyncing && !isPaused)}
        >
          <CircleSlash className="h-4 w-4 mr-1" />
          {t('unsyncFromCursor')}
        </Button>

        <Button
          variant="outline"
          size="sm"
          onClick={onEditSelectedWord}
          disabled={!hasSelectedWords || selectedWordCount !== 1}
        >
          <Edit3 className="h-4 w-4 mr-1" />
          {t('editWord')}
        </Button>

        <Button
          variant="outline"
          size="sm"
          onClick={onDeleteSelected}
          disabled={!hasSelectedWords || (isManualSyncing && !isPaused)}
          className="text-destructive hover:text-destructive"
        >
          <Trash2 className="h-4 w-4 mr-1" />
          {hasSelectedWords && selectedWordCount > 0
            ? t('deleteCount', { count: selectedWordCount })
            : t('deleteCount', { count: selectedWordCount }).split(' (')[0]}
        </Button>
      </div>
    </div>
  )
})

export default SyncControls
