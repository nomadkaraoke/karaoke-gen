'use client'

import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
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

  const minutes = durationSeconds != null ? formatDurationCost(durationSeconds).minutes : null
  const canAfford = balance >= credits

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose() }}>
      <DialogContent className="sm:max-w-md bg-card border-border" showCloseButton={false} aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle className="text-foreground">
            {reconcile ? t('reconcileTitle') : t('confirmTitle')}
          </DialogTitle>
        </DialogHeader>

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
            <Button onClick={onBuyCredits} disabled={!onBuyCredits}>
              {t('buyCredits')}
            </Button>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
