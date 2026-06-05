'use client'

import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import { formatDurationCost } from '@/lib/pricing'

interface Props {
  open: boolean
  durationSeconds: number | null
  credits: number
  balance: number
  estimated?: boolean
  reconcile?: boolean
  onConfirm: () => void
  onClose: () => void
  onBuyCredits?: () => void
}

export function DurationCostConfirm({
  open,
  durationSeconds,
  credits,
  balance,
  estimated = false,
  reconcile = false,
  onConfirm,
  onClose,
  onBuyCredits,
}: Props) {
  const t = useTranslations('pricing')

  if (!open) return null

  const minutes = durationSeconds != null ? formatDurationCost(durationSeconds).minutes : null
  const canAfford = balance >= credits

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80"
      role="dialog"
      aria-modal="true"
    >
      <div className="bg-card border border-border rounded-lg shadow-lg w-full max-w-md p-6 space-y-4">
        <h2 className="text-lg font-semibold text-foreground">
          {reconcile ? t('reconcileTitle') : t('confirmTitle')}
        </h2>

        <div className="space-y-2 text-sm text-foreground">
          <p>
            {t('creditsForDuration', {
              minutes: minutes ?? '—',
              credits,
            })}
          </p>

          {estimated && (
            <p className="text-muted-foreground text-xs">
              {t('estimatedLabel')}
            </p>
          )}

          <p className="text-muted-foreground">
            {t('balance', { balance })}
          </p>
        </div>

        <div className="flex gap-3 justify-end pt-2">
          <Button variant="ghost" onClick={onClose}>
            {t('cancel')}
          </Button>

          {canAfford ? (
            <Button onClick={onConfirm}>
              {t('confirm')}
            </Button>
          ) : (
            <Button onClick={onBuyCredits}>
              {t('buyCredits')}
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}
