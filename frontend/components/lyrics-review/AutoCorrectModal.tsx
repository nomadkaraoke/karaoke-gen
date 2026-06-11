'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Sparkles, Loader2, AlertTriangle } from 'lucide-react'
import {
  AutoCorrectSettings,
  DEFAULT_AUTO_CORRECT_SETTINGS,
} from '@/lib/api/autoCorrect'
import type { AutoCorrectStatus } from '@/hooks/useAutoCorrect'

interface AutoCorrectModalProps {
  open: boolean
  onClose: () => void
  onRun: (settings: AutoCorrectSettings) => void
  onCancelRun: () => void
  status: AutoCorrectStatus
  error: string | null
  hasReferences: boolean
}

const CONFIDENCE_PRESETS = ['all', 'balanced', 'high'] as const
type ConfidencePreset = (typeof CONFIDENCE_PRESETS)[number]
const PRESET_THRESHOLDS: Record<ConfidencePreset, number> = {
  all: 0,
  balanced: 0.5,
  high: 0.8,
}

export default function AutoCorrectModal({
  open,
  onClose,
  onRun,
  onCancelRun,
  status,
  error,
  hasReferences,
}: AutoCorrectModalProps) {
  const t = useTranslations('lyricsReview.autoCorrect')
  const [adlibRemoval, setAdlibRemoval] = useState(
    DEFAULT_AUTO_CORRECT_SETTINGS.suggest_adlib_removal,
  )
  const [allowInsertions, setAllowInsertions] = useState(
    DEFAULT_AUTO_CORRECT_SETTINGS.allow_insertions,
  )
  const [confidencePreset, setConfidencePreset] = useState<ConfidencePreset>('all')

  const isRunning = status === 'running'

  const handleRun = () => {
    onRun({
      suggest_adlib_removal: adlibRemoval,
      allow_insertions: allowInsertions,
      min_confidence: PRESET_THRESHOLDS[confidencePreset],
      // Always multi-model — kept aligned with the backend proactive run so
      // the cached result is reused.
      compare_models: DEFAULT_AUTO_CORRECT_SETTINGS.compare_models,
    })
  }

  const handleClose = () => {
    if (isRunning) onCancelRun()
    onClose()
  }

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && handleClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-purple-500" />
            {t('title')}
          </DialogTitle>
          <DialogDescription>{t('description')}</DialogDescription>
        </DialogHeader>

        {!hasReferences ? (
          <div className="flex items-start gap-2 rounded-md border border-amber-500/50 bg-amber-500/10 p-3 text-sm">
            <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0 text-amber-500" />
            <span>{t('noReferences')}</span>
          </div>
        ) : isRunning ? (
          <div className="flex flex-col items-center gap-3 py-6">
            <Loader2 className="h-8 w-8 animate-spin text-purple-500" />
            <p className="text-sm text-muted-foreground">{t('running')}</p>
          </div>
        ) : (
          <div className="space-y-4">
            <div className="flex items-center justify-between gap-4">
              <div>
                <Label htmlFor="ac-adlib">{t('adlibRemovalLabel')}</Label>
                <p className="text-xs text-muted-foreground">
                  {t('adlibRemovalHint')}
                </p>
              </div>
              <Switch
                id="ac-adlib"
                checked={adlibRemoval}
                onCheckedChange={setAdlibRemoval}
              />
            </div>

            <div className="flex items-center justify-between gap-4">
              <div>
                <Label htmlFor="ac-insertions">{t('insertionsLabel')}</Label>
                <p className="text-xs text-muted-foreground">
                  {t('insertionsHint')}
                </p>
              </div>
              <Switch
                id="ac-insertions"
                checked={allowInsertions}
                onCheckedChange={setAllowInsertions}
              />
            </div>

            <div className="flex items-center justify-between gap-4">
              <div>
                <Label>{t('confidenceLabel')}</Label>
                <p className="text-xs text-muted-foreground">
                  {t('confidenceHint')}
                </p>
              </div>
              <Select
                value={confidencePreset}
                onValueChange={(v) => setConfidencePreset(v as ConfidencePreset)}
              >
                <SelectTrigger className="w-36">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CONFIDENCE_PRESETS.map((p) => (
                    <SelectItem key={p} value={p}>
                      {t(`confidence_${p}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            {error && (
              <div className="flex items-start gap-2 rounded-md border border-destructive/50 bg-destructive/10 p-3 text-sm">
                <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0 text-destructive" />
                <span>{error}</span>
              </div>
            )}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={handleClose}>
            {isRunning ? t('cancel') : t('close')}
          </Button>
          {!isRunning && hasReferences && (
            <Button onClick={handleRun}>
              <Sparkles className="h-4 w-4 mr-2" />
              {t('runButton')}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
