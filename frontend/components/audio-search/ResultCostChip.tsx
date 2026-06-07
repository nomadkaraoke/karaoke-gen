"use client"

import { useTranslations } from 'next-intl'
import { durationToCredits, isBlocked } from '@/lib/pricing'

interface ResultCostChipProps {
  durationSeconds: number | undefined | null
}

/**
 * Small credit-cost badge for an audio-search result row.
 * Renders nothing when duration is unknown.
 */
export function ResultCostChip({ durationSeconds }: ResultCostChipProps) {
  const t = useTranslations('audioSearch')

  if (durationSeconds == null) return null

  const credits = durationToCredits(durationSeconds)
  const blocked = isBlocked(durationSeconds)

  return (
    <span
      data-testid="result-cost-chip"
      className={`text-[8px] px-1 py-0.5 rounded font-medium ${
        blocked
          ? 'bg-orange-600/20 text-orange-400'
          : 'bg-blue-600/20 text-blue-400'
      }`}
    >
      {t('creditCost', { credits })}
    </span>
  )
}
