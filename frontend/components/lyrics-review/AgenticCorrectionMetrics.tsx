'use client'

import { useMemo } from 'react'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { WordCorrection } from '@/lib/lyrics-review/types'
import { cn } from '@/lib/utils'

interface GapCategoryMetric {
  category: string
  count: number
  avgConfidence: number
  corrections: WordCorrection[]
}

interface AgenticCorrectionMetricsProps {
  corrections: WordCorrection[]
  onCategoryClick?: (category: string) => void
  onConfidenceFilterClick?: (filter: 'low' | 'high') => void
}

export default function AgenticCorrectionMetrics({
  corrections,
  onCategoryClick,
  onConfidenceFilterClick,
}: AgenticCorrectionMetricsProps) {
  const metrics = useMemo(() => {
    // Filter only agentic corrections
    const agenticCorrections = corrections.filter((c) => c.handler === 'AgenticCorrector')

    // Parse category from reason string (format: "reason [CATEGORY] (confidence: XX%)")
    const categoryMap = new Map<string, GapCategoryMetric>()

    agenticCorrections.forEach((correction) => {
      const categoryMatch = correction.reason?.match(/\[([A-Z_]+)\]/)
      const category = categoryMatch ? categoryMatch[1] : 'UNKNOWN'

      if (!categoryMap.has(category)) {
        categoryMap.set(category, {
          category,
          count: 0,
          avgConfidence: 0,
          corrections: [],
        })
      }

      const metric = categoryMap.get(category)!
      metric.count++
      metric.corrections.push(correction)
    })

    // Calculate average confidence for each category
    categoryMap.forEach((metric) => {
      const totalConfidence = metric.corrections.reduce((sum, c) => sum + c.confidence, 0)
      metric.avgConfidence = totalConfidence / metric.count
    })

    // Convert to array and sort by count descending
    const sortedMetrics = Array.from(categoryMap.values()).sort((a, b) => b.count - a.count)

    // Calculate overall stats
    const totalCorrections = agenticCorrections.length
    const avgConfidence =
      totalCorrections > 0
        ? agenticCorrections.reduce((sum, c) => sum + c.confidence, 0) / totalCorrections
        : 0

    const lowConfidenceCount = agenticCorrections.filter((c) => c.confidence < 0.6).length
    const highConfidenceCount = agenticCorrections.filter((c) => c.confidence >= 0.8).length

    return {
      categories: sortedMetrics,
      totalCorrections,
      avgConfidence,
      lowConfidenceCount,
      highConfidenceCount,
    }
  }, [corrections])

  // Format category name for display
  const formatCategory = (category: string): string => {
    return category
      .split('_')
      .map((word) => word.charAt(0) + word.slice(1).toLowerCase())
      .join(' ')
  }

  // Get emoji/icon for category
  const getCategoryIcon = (category: string): string => {
    const icons: Record<string, string> = {
      SOUND_ALIKE: '',
      PUNCTUATION_ONLY: '',
      BACKGROUND_VOCALS: '',
      EXTRA_WORDS: '',
      REPEATED_SECTION: '',
      COMPLEX_MULTI_ERROR: '',
      AMBIGUOUS: '',
      NO_ERROR: '',
    }
    return icons[category] || ''
  }

  return (
    <TooltipProvider>
      <Card className="p-2 h-full flex flex-col">
        <span className="text-xs text-muted-foreground mb-1">Agentic AI Corrections</span>

        {/* Overall stats */}
        <div className="mb-2">
          <p className="text-xs mb-1">
            Total: <strong>{metrics.totalCorrections}</strong>
          </p>
          <p className="text-xs mb-1">
            Avg Confidence: <strong>{(metrics.avgConfidence * 100).toFixed(0)}%</strong>
          </p>

          {/* Quick filters */}
          <div className="flex gap-1 flex-wrap">
            <Badge
              variant="outline"
              className="text-[0.65rem] h-5 cursor-pointer hover:bg-muted/30"
              onClick={() => onConfidenceFilterClick?.('low')}
            >
              Low (&lt;60%): {metrics.lowConfidenceCount}
            </Badge>
            <Badge
              variant="outline"
              className="text-[0.65rem] h-5 cursor-pointer hover:bg-muted/30 text-green-600"
              onClick={() => onConfidenceFilterClick?.('high')}
            >
              High (&ge;80%): {metrics.highConfidenceCount}
            </Badge>
          </div>
        </div>

        {/* Category breakdown */}
        <span className="text-xs text-muted-foreground mb-1">By Category</span>
        <div className="flex-1 overflow-auto">
          {metrics.categories.map((metric) => (
            <Tooltip key={metric.category}>
              <TooltipTrigger asChild>
                <div
                  className="mb-1 p-1 rounded cursor-pointer hover:bg-muted/30 transition-colors"
                  onClick={() => onCategoryClick?.(metric.category)}
                >
                  <div className="flex justify-between items-center">
                    <div className="flex items-center gap-1">
                      <span className="text-sm">{getCategoryIcon(metric.category)}</span>
                      <span className="text-xs">{formatCategory(metric.category)}</span>
                    </div>
                    <div className="flex gap-1 items-center">
                      <span className="text-[0.65rem] text-muted-foreground">
                        {(metric.avgConfidence * 100).toFixed(0)}%
                      </span>
                      <span className="text-xs font-semibold bg-accent px-1 rounded min-w-[24px] text-center">
                        {metric.count}
                      </span>
                    </div>
                  </div>
                </div>
              </TooltipTrigger>
              <TooltipContent side="right">
                <p>
                  {metric.count} correction{metric.count !== 1 ? 's' : ''} • Avg confidence:{' '}
                  {(metric.avgConfidence * 100).toFixed(0)}%
                </p>
              </TooltipContent>
            </Tooltip>
          ))}
          {metrics.categories.length === 0 && (
            <p className="text-xs text-muted-foreground italic">No agentic corrections</p>
          )}
        </div>
      </Card>
    </TooltipProvider>
  )
}
