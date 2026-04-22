'use client'

import { useTranslations } from 'next-intl'
import { cn } from '@/lib/utils'
import type { SingerId } from '@/lib/lyrics-review/types'
import { cycleSinger } from '@/lib/lyrics-review/duet'

interface SingerChipProps {
  singer: SingerId
  hasOverrides: boolean
  onChange: (next: SingerId) => void
  className?: string
}

const SINGER_LABEL: Record<SingerId, string> = { 1: '1', 2: '2', 0: 'Both' }

const SINGER_CHIP_CLASSES: Record<SingerId, string> = {
  1: 'bg-blue-900/40 border-blue-500 text-blue-200',
  2: 'bg-pink-900/40 border-pink-500 text-pink-200',
  0: 'bg-yellow-900/40 border-yellow-500 text-yellow-100',
}

export default function SingerChip({ singer, hasOverrides, onChange, className }: SingerChipProps) {
  const t = useTranslations('lyricsReview.duet')

  const label = SINGER_LABEL[singer]
  const ariaLabel = hasOverrides
    ? t('singerChipAriaLabelWithOverrides')
    : t('singerChipAriaLabel')

  return (
    <button
      type="button"
      onClick={() => onChange(cycleSinger(singer))}
      className={cn(
        'inline-flex items-center gap-1 px-2 py-0 rounded-sm text-[0.7rem] font-semibold border cursor-pointer select-none',
        SINGER_CHIP_CLASSES[singer],
        className,
      )}
      aria-label={ariaLabel}
    >
      <span>●</span>
      <span>{label}{hasOverrides ? '*' : ''}</span>
    </button>
  )
}
