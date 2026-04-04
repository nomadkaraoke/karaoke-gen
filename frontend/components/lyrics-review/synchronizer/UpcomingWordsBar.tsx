'use client'

import { useTranslations } from 'next-intl'
import { memo, useMemo } from 'react'
import { Word } from '@/lib/lyrics-review/types'
import { cn } from '@/lib/utils'

interface UpcomingWordsBarProps {
  words: Word[]
  syncWordIndex: number
  isManualSyncing: boolean
  maxWordsToShow?: number
}

const UpcomingWordsBar = memo(function UpcomingWordsBar({
  words,
  syncWordIndex,
  isManualSyncing,
  maxWordsToShow = 20,
}: UpcomingWordsBarProps) {
  const t = useTranslations('lyricsReview.synchronizer')
  // Get upcoming unsynced words
  const upcomingWords = useMemo(() => {
    if (!isManualSyncing || syncWordIndex < 0) return []

    return words
      .slice(syncWordIndex)
      .filter((w) => w.start_time === null)
      .slice(0, maxWordsToShow)
  }, [words, syncWordIndex, isManualSyncing, maxWordsToShow])

  const totalRemaining = useMemo(() => {
    if (!isManualSyncing || syncWordIndex < 0) return 0
    return words.slice(syncWordIndex).filter((w) => w.start_time === null).length
  }, [words, syncWordIndex, isManualSyncing])

  // Show empty placeholder when no upcoming words
  if (upcomingWords.length === 0) {
    return null
  }

  return (
    <div className="h-11 bg-muted rounded flex items-center px-2 gap-1 overflow-hidden">
      {upcomingWords.map((word, index) => (
        <span
          key={word.id}
          className={cn(
            'px-2 py-1 rounded text-sm whitespace-nowrap',
            index === 0
              ? 'bg-destructive text-destructive-foreground font-bold border-2 border-destructive'
              : 'bg-muted-foreground/20 text-foreground'
          )}
        >
          {word.text}
        </span>
      ))}

      {totalRemaining > maxWordsToShow && (
        <span className="text-xs text-muted-foreground ml-2">
          {t('upcomingMore', { count: totalRemaining - maxWordsToShow })}
        </span>
      )}
    </div>
  )
})

export default UpcomingWordsBar
