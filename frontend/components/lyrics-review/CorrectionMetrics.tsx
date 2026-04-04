'use client'

import { useTranslations } from 'next-intl'
import { Card } from '@/components/ui/card'
import { COLORS } from '@/lib/lyrics-review/constants'
import { cn } from '@/lib/utils'

interface MetricProps {
  color?: string
  label: string
  value: string | number
  description: string
  details?: Array<{ label: string; value: string | number }>
  onClick?: () => void
}

function Metric({ color, label, value, description, details, onClick }: MetricProps) {
  return (
    <Card
      className={cn(
        'p-2 pt-0 h-full flex flex-col',
        onClick && 'cursor-pointer hover:bg-muted/30 transition-colors'
      )}
      onClick={onClick}
    >
      <div className="flex items-center gap-1 mb-1 mt-2">
        {color && (
          <div
            className="w-3 h-3 rounded"
            style={{ backgroundColor: color }}
          />
        )}
        <span className="text-xs text-muted-foreground">{label}</span>
      </div>
      <div className="flex items-baseline gap-1 mb-1">
        <span className="text-lg font-semibold">{value}</span>
      </div>
      <span className="text-xs text-muted-foreground">{description}</span>
      {details && (
        <div className="mt-1 flex-1 overflow-auto">
          {details.map((detail, index) => (
            <div key={index} className="flex justify-between mt-1">
              <span className="text-xs text-muted-foreground">{detail.label}</span>
              <span className="text-xs">{detail.value}</span>
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}

interface CorrectionMetricsProps {
  // Anchor metrics
  anchorCount?: number
  multiSourceAnchors?: number
  anchorWordCount?: number
  // Gap metrics
  correctedGapCount?: number
  uncorrectedGapCount?: number
  uncorrectedGaps?: Array<{
    position: string
    length: number
  }>
  // Correction details
  replacedCount?: number
  addedCount?: number
  deletedCount?: number
  // Add total words count
  totalWords?: number
  onMetricClick?: {
    anchor?: () => void
    corrected?: () => void
    uncorrected?: () => void
  }
}

export default function CorrectionMetrics({
  anchorCount,
  multiSourceAnchors = 0,
  anchorWordCount = 0,
  correctedGapCount = 0,
  uncorrectedGapCount = 0,
  uncorrectedGaps = [],
  replacedCount = 0,
  addedCount = 0,
  deletedCount = 0,
  totalWords = 0,
  onMetricClick,
}: CorrectionMetricsProps) {
  const t = useTranslations('lyricsReview.guidance')
  // Calculate percentages based on word counts
  const anchorPercentage = totalWords > 0 ? Math.round((anchorWordCount / totalWords) * 100) : 0
  const uncorrectedWordCount = uncorrectedGaps?.reduce((sum, gap) => sum + gap.length, 0) ?? 0
  const uncorrectedPercentage =
    totalWords > 0 ? Math.round((uncorrectedWordCount / totalWords) * 100) : 0
  const correctedWordCount = replacedCount + addedCount
  const correctedPercentage =
    totalWords > 0 ? Math.round((correctedWordCount / totalWords) * 100) : 0

  return (
    <div className="h-full flex">
      <div className="flex-1 flex flex-row gap-2 h-full">
        <div className="flex-1 h-full">
          <Metric
            color={COLORS.anchor}
            label={t('anchorSequences')}
            value={`${anchorCount ?? '-'} (${anchorPercentage}%)`}
            description={t('anchorSequencesDesc')}
            details={[
              { label: t('wordsInAnchors'), value: anchorWordCount },
              { label: t('multiSourceMatches'), value: multiSourceAnchors },
            ]}
            onClick={onMetricClick?.anchor}
          />
        </div>
        <div className="flex-1 h-full">
          <Metric
            color={COLORS.corrected}
            label={t('correctedGaps')}
            value={`${correctedGapCount} (${correctedPercentage}%)`}
            description={t('correctedGapsDesc')}
            details={[
              { label: t('wordsReplaced'), value: replacedCount },
              { label: t('wordsAddedDeleted'), value: `+${addedCount} / -${deletedCount}` },
            ]}
            onClick={onMetricClick?.corrected}
          />
        </div>
        <div className="flex-1 h-full">
          <Metric
            color={COLORS.uncorrectedGap}
            label={t('uncorrectedGaps')}
            value={`${uncorrectedGapCount} (${uncorrectedPercentage}%)`}
            description={t('uncorrectedGapsDesc')}
            details={[
              { label: t('wordsUncorrected'), value: uncorrectedWordCount },
              { label: t('numberOfGaps'), value: uncorrectedGaps.length },
            ]}
            onClick={onMetricClick?.uncorrected}
          />
        </div>
      </div>
    </div>
  )
}
