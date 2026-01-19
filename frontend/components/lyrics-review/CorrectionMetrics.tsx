'use client'

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
            label="Anchor Sequences"
            value={`${anchorCount ?? '-'} (${anchorPercentage}%)`}
            description="Matched sections between transcription and reference"
            details={[
              { label: 'Words in Anchors', value: anchorWordCount },
              { label: 'Multi-source Matches', value: multiSourceAnchors },
            ]}
            onClick={onMetricClick?.anchor}
          />
        </div>
        <div className="flex-1 h-full">
          <Metric
            color={COLORS.corrected}
            label="Corrected Gaps"
            value={`${correctedGapCount} (${correctedPercentage}%)`}
            description="Successfully corrected sections"
            details={[
              { label: 'Words Replaced', value: replacedCount },
              { label: 'Words Added / Deleted', value: `+${addedCount} / -${deletedCount}` },
            ]}
            onClick={onMetricClick?.corrected}
          />
        </div>
        <div className="flex-1 h-full">
          <Metric
            color={COLORS.uncorrectedGap}
            label="Uncorrected Gaps"
            value={`${uncorrectedGapCount} (${uncorrectedPercentage}%)`}
            description="Sections that may need manual review"
            details={[
              { label: 'Words Uncorrected', value: uncorrectedWordCount },
              { label: 'Number of Gaps', value: uncorrectedGaps.length },
            ]}
            onClick={onMetricClick?.uncorrected}
          />
        </div>
      </div>
    </div>
  )
}
