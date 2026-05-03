'use client'

import { useTranslations } from 'next-intl'
import { useMemo } from 'react'
import { AlertTriangle, ChevronDown } from 'lucide-react'
import { Switch } from '@/components/ui/switch'
import { Label } from '@/components/ui/label'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import {
  GenerationSettings,
  StrictnessLevel,
} from '@/lib/api/customLyrics'
import { cn } from '@/lib/utils'

const STRICTNESS_ORDER: StrictnessLevel[] = [
  'verbatim',
  'loose',
  'balanced',
  'tight',
  'strict',
]

interface Props {
  settings: GenerationSettings
  onChange: (next: GenerationSettings) => void
  disabled?: boolean
}

export default function CustomLyricsSettings({ settings, onChange, disabled = false }: Props) {
  const t = useTranslations('lyricsReview.modals.customLyricsMode.settings')

  const showContradiction = useMemo(
    () => !settings.allow_reword && (settings.strictness === 'strict' || settings.strictness === 'tight'),
    [settings.allow_reword, settings.strictness],
  )

  const set = <K extends keyof GenerationSettings>(key: K, value: GenerationSettings[K]) => {
    onChange({ ...settings, [key]: value })
  }

  const labels: Record<StrictnessLevel, string> = {
    verbatim: t('strictnessVerbatim'),
    loose: t('strictnessLoose'),
    balanced: t('strictnessBalanced'),
    tight: t('strictnessTight'),
    strict: t('strictnessStrict'),
  }

  const hints: Record<StrictnessLevel, string> = {
    verbatim: t('strictnessHintVerbatim'),
    loose: t('strictnessHintLoose'),
    balanced: t('strictnessHintBalanced'),
    tight: t('strictnessHintTight'),
    strict: t('strictnessHintStrict'),
  }

  return (
    <Collapsible defaultOpen className="border rounded-md">
      <CollapsibleTrigger className="flex w-full items-center justify-between p-3 text-sm font-medium">
        {t('title')}
        <ChevronDown className="h-4 w-4 transition-transform [&[data-state=open]]:rotate-180" />
      </CollapsibleTrigger>
      <CollapsibleContent className="px-3 pb-3 space-y-3">
        <ToggleRow
          name="allow_reword"
          label={t('allowReword')}
          hint={t('allowRewordHint')}
          checked={settings.allow_reword}
          onChange={(v) => set('allow_reword', v)}
          disabled={disabled}
        />
        <ToggleRow
          name="allow_omit"
          label={t('allowOmit')}
          hint={t('allowOmitHint')}
          checked={settings.allow_omit}
          onChange={(v) => set('allow_omit', v)}
          disabled={disabled}
        />
        <ToggleRow
          name="fixed_line_count"
          label={t('fixedLineCount')}
          hint={t('fixedLineCountHint')}
          checked={settings.fixed_line_count}
          onChange={(v) => set('fixed_line_count', v)}
          disabled={disabled}
        />

        <div className="pt-2">
          <Label id="strictness-label" className="text-sm font-medium">{t('strictness')}</Label>
          <div role="group" aria-labelledby="strictness-label" className="mt-2 grid grid-cols-5 gap-1">
            {STRICTNESS_ORDER.map((level) => (
              <button
                key={level}
                type="button"
                disabled={disabled}
                onClick={() => set('strictness', level)}
                aria-pressed={settings.strictness === level}
                className={cn(
                  'rounded-md text-xs py-1.5 transition-colors',
                  settings.strictness === level
                    ? 'bg-primary text-primary-foreground'
                    : 'bg-muted hover:bg-muted/70',
                )}
              >
                {labels[level]}
              </button>
            ))}
          </div>
          <p className="mt-2 text-xs text-muted-foreground">
            {hints[settings.strictness]}
          </p>
        </div>

        {showContradiction && (
          <div className="flex items-start gap-2 p-2 rounded-md bg-yellow-500/10 text-xs text-yellow-700 dark:text-yellow-300">
            <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
            <span>{t('contradictionWarning')}</span>
          </div>
        )}
      </CollapsibleContent>
    </Collapsible>
  )
}

interface ToggleRowProps {
  name: string
  label: string
  hint: string
  checked: boolean
  onChange: (value: boolean) => void
  disabled?: boolean
}

function ToggleRow({ name, label, hint, checked, onChange, disabled }: ToggleRowProps) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div className="flex-1">
        <Label htmlFor={name} className="text-sm font-medium">
          {label}
        </Label>
        <p className="text-xs text-muted-foreground mt-0.5">{hint}</p>
      </div>
      <Switch
        id={name}
        checked={checked}
        onCheckedChange={onChange}
        disabled={disabled}
      />
    </div>
  )
}
