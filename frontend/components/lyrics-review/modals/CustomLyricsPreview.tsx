'use client'

import { useTranslations } from 'next-intl'
import { useMemo } from 'react'
import { Check, AlertTriangle, Info } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { cn } from '@/lib/utils'
import type { LineMetadata, LineSeverity, StopReason } from '@/lib/api/customLyrics'

interface Props {
  lines: string[]
  lineMetadata: LineMetadata[]
  iterationsUsed: number
  stopReason: StopReason
  expectedLineCount: number
  onLineEdit: (index: number, text: string) => void
}

export default function CustomLyricsPreview({
  lines,
  lineMetadata,
  iterationsUsed,
  stopReason,
  expectedLineCount,
  onLineEdit,
}: Props) {
  const t = useTranslations('lyricsReview.modals.customLyricsMode.preview')

  const passing = useMemo(
    () => lineMetadata.filter((m) => m.passes).length,
    [lineMetadata],
  )

  const stopMessage = useMemo(() => {
    switch (stopReason) {
      case 'success': return t('stopReasonSuccess')
      case 'plateau': return t('stopReasonPlateau')
      case 'max_iters_reached': return t('stopReasonMaxIters')
      case 'line_count_mismatch': return t('stopReasonLineCountMismatch')
      case 'verbatim_skip': return t('stopReasonVerbatimSkip')
    }
  }, [stopReason, t])

  const showVariableBanner = lines.length !== expectedLineCount

  return (
    <div className="flex flex-col gap-3 flex-1 overflow-hidden">
      <div className="flex items-center gap-3 text-xs text-muted-foreground">
        <span>{t('iterationsBadge', { n: iterationsUsed })}</span>
        <span>•</span>
        <span>{t('linesPassingBadge', { passing, total: lineMetadata.length })}</span>
      </div>

      {stopMessage && (
        <div className="flex items-start gap-2 p-2 rounded-md bg-muted/50 text-xs">
          <Info className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          <span>{stopMessage}</span>
        </div>
      )}

      {showVariableBanner && (
        <div className="flex items-start gap-2 p-2 rounded-md bg-blue-500/10 text-xs text-blue-700 dark:text-blue-300">
          <Info className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          <span>
            {t('variableLineCountBanner', { actual: lines.length, expected: expectedLineCount })}
          </span>
        </div>
      )}

      <div className="flex-1 overflow-y-auto space-y-1 pr-1">
        {lines.map((line, idx) => {
          const meta = lineMetadata[idx]
          return (
            <LineRow
              key={idx}
              index={idx}
              text={line}
              meta={meta}
              onEdit={(value) => onLineEdit(idx, value)}
            />
          )
        })}
      </div>
    </div>
  )
}

interface LineRowProps {
  index: number
  text: string
  meta: LineMetadata | undefined
  onEdit: (value: string) => void
}

function LineRow({ index, text, meta, onEdit }: LineRowProps) {
  const t = useTranslations('lyricsReview.modals.customLyricsMode.preview')
  const severity: LineSeverity = meta?.severity ?? 'ok'
  const colorClass = severityClass(severity)
  const summary = meta
    ? `${t('syllableSummary', { target: median(meta.target_syllables), actual: median(meta.candidate_syllables) })} · ${t('syllableDelta', { delta: meta.min_delta })}`
    : ''

  return (
    <div className="flex items-center gap-2">
      <span className="w-8 text-xs text-muted-foreground tabular-nums shrink-0">
        {index + 1}
      </span>
      <Input
        value={text}
        onChange={(e) => onEdit(e.target.value)}
        aria-label={t('lineNumberLabel', { n: index + 1 })}
        className={cn('flex-1 font-mono text-sm h-8', colorClass)}
      />
      <span className="text-xs text-muted-foreground w-44 shrink-0 text-right">{summary}</span>
      <SeverityBadge severity={severity} />
    </div>
  )
}

function median(arr: number[]): number {
  if (!arr.length) return 0
  const sorted = [...arr].sort((a, b) => a - b)
  return sorted[Math.floor(sorted.length / 2)]
}

function severityClass(s: LineSeverity): string {
  if (s === 'major') return 'border-destructive'
  if (s === 'minor') return 'border-yellow-500'
  return ''
}

function SeverityBadge({ severity }: { severity: LineSeverity }) {
  const t = useTranslations('lyricsReview.modals.customLyricsMode.preview')
  if (severity === 'ok') return <Check className="h-4 w-4 text-green-500 shrink-0" aria-label={t('severityOk')} role="img" />
  if (severity === 'minor') return <AlertTriangle className="h-4 w-4 text-yellow-500 shrink-0" aria-label={t('severityMinor')} role="img" />
  return <AlertTriangle className="h-4 w-4 text-destructive shrink-0" aria-label={t('severityMajor')} role="img" />
}
