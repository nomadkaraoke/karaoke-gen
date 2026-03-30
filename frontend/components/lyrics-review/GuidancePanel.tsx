'use client'

import { useState } from 'react'
import { Card } from '@/components/ui/card'
import { Switch } from '@/components/ui/switch'
import { Label } from '@/components/ui/label'
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { ExternalLink, Lightbulb, X } from 'lucide-react'
import { CorrectionData } from '@/lib/lyrics-review/types'
import { cn } from '@/lib/utils'

interface GuidancePanelProps {
  data: CorrectionData
  isReadOnly: boolean
  onMetricClick: {
    anchor: () => void
    corrected: () => void
    uncorrected: () => void
  }
  onHandlerToggle: (handler: string, enabled: boolean) => void
  isUpdatingHandlers: boolean
  onHandlerClick?: (handler: string) => void
  statsVisible: boolean
  onStatsToggle: () => void
}

export default function GuidancePanel({
  data,
  isReadOnly,
  onMetricClick,
  onHandlerToggle,
  isUpdatingHandlers,
  onHandlerClick,
  statsVisible,
  onStatsToggle,
}: GuidancePanelProps) {
  const [guidanceDismissed, setGuidanceDismissed] = useState(() => {
    if (typeof window === 'undefined') return false
    return localStorage.getItem('lyricsReviewGuidanceDismissed') === 'true'
  })

  const isMobile = typeof window !== 'undefined' && window.innerWidth < 768

  // Calculate metrics
  const gapCorrections = data.corrections.reduce(
    (map: Record<string, number>, correction) => {
      const gap = data.gap_sequences.find((g) =>
        g.transcribed_word_ids.includes(correction.word_id)
      )
      if (gap) {
        map[gap.id] = (map[gap.id] || 0) + 1
      }
      return map
    },
    {} as Record<string, number>
  )

  const correctedGapCount = Object.keys(gapCorrections).length
  const uncorrectedGapCount = data.gap_sequences.length - correctedGapCount
  const totalGaps = data.gap_sequences.length

  // Handler info
  const handlerCounts =
    data.corrections?.reduce(
      (counts: Record<string, number>, correction) => {
        counts[correction.handler] = (counts[correction.handler] || 0) + 1
        return counts
      },
      {} as Record<string, number>
    ) || {}

  const availableHandlers = data.metadata.available_handlers || []
  const enabledHandlers = new Set(data.metadata.enabled_handlers || [])

  // Detailed metrics
  const totalWords = data.metadata.total_words || 0
  const anchorWordCount = data.anchor_sequences?.reduce(
    (sum, anchor) => sum + (anchor.transcribed_word_ids?.length || 0),
    0
  ) ?? 0
  const multiSourceAnchors = data.anchor_sequences?.filter(
    (anchor) =>
      anchor?.reference_word_ids && Object.keys(anchor.reference_word_ids).length > 1
  ).length ?? 0
  const replacedCount = data.corrections.filter(
    (c) => !c.is_deletion && !c.split_total
  ).length
  const addedCount = data.corrections.filter((c) => c.split_total).length
  const deletedCount = data.corrections.filter((c) => c.is_deletion).length
  const uncorrectedGaps = data.gap_sequences.filter(
    (gap) => !gapCorrections[gap.id] && gap.transcribed_word_ids.length > 0
  )
  const uncorrectedWordCount = uncorrectedGaps.reduce(
    (sum, gap) => sum + gap.transcribed_word_ids.length,
    0
  )
  const anchorPercentage = totalWords > 0 ? Math.round((anchorWordCount / totalWords) * 100) : 0
  const correctedWordCount = replacedCount + addedCount
  const correctedPercentage = totalWords > 0 ? Math.round((correctedWordCount / totalWords) * 100) : 0
  const uncorrectedPercentage = totalWords > 0 ? Math.round((uncorrectedWordCount / totalWords) * 100) : 0

  const handleDismiss = () => {
    setGuidanceDismissed(true)
    if (typeof window !== 'undefined') {
      localStorage.setItem('lyricsReviewGuidanceDismissed', 'true')
    }
  }

  const handleShowTips = () => {
    setGuidanceDismissed(false)
    if (typeof window !== 'undefined') {
      localStorage.removeItem('lyricsReviewGuidanceDismissed')
    }
  }

  return (
    <div className="space-y-2 mb-2">
      {/* Color legend row */}
      <div className="flex items-center gap-3 text-xs flex-wrap">
        <button
          className="flex items-center gap-1.5 hover:opacity-80 transition-opacity"
          onClick={onMetricClick.anchor}
        >
          <span className="w-2.5 h-2.5 rounded-full bg-blue-500/60 inline-block" />
          <span className="text-muted-foreground">Matched</span>
        </button>
        <button
          className="flex items-center gap-1.5 hover:opacity-80 transition-opacity"
          onClick={onMetricClick.uncorrected}
        >
          <span className="w-2.5 h-2.5 rounded-full bg-orange-500/60 inline-block" />
          <span className="text-muted-foreground">Gaps (needs review)</span>
        </button>
        <button
          className="flex items-center gap-1.5 hover:opacity-80 transition-opacity"
          onClick={onMetricClick.corrected}
        >
          <span className="w-2.5 h-2.5 rounded-full bg-green-500/60 inline-block" />
          <span className="text-muted-foreground">Corrected</span>
        </button>

        <div className="flex items-center gap-2 ml-auto">
          {guidanceDismissed && !isReadOnly && (
            <button
              className="text-amber-500 hover:text-amber-400 transition-colors text-xs"
              onClick={handleShowTips}
            >
              Show tips
            </button>
          )}
          <button
            className={cn(
              'text-xs px-1.5 py-0.5 rounded border transition-colors',
              statsVisible
                ? 'border-primary text-primary'
                : 'border-muted text-muted-foreground hover:text-foreground hover:border-foreground/50'
            )}
            onClick={onStatsToggle}
          >
            {statsVisible ? 'Hide Stats' : 'Stats'}
          </button>
        </div>
      </div>

      {/* Workflow tips (dismissible) */}
      {!guidanceDismissed && !isReadOnly && (
        <div className="flex items-start gap-2 p-2.5 rounded-md bg-amber-500/10 border border-amber-500/20">
          <Lightbulb className="h-4 w-4 text-amber-500 shrink-0 mt-0.5" />
          <div className="text-xs text-muted-foreground flex-1 leading-relaxed space-y-1.5">
            {totalGaps === 0 && Object.keys(data.reference_lyrics).length === 0 ? (
              <p>No reference lyrics were found for this track. You can still review the transcription and click any word to edit. Use the <strong>Reference Lyrics</strong> panel on the right to search for lyrics with a different artist/title.</p>
            ) : totalGaps === 0 ? (
              <p>All lyrics matched the reference — no corrections needed. You can still click any word to edit.</p>
            ) : (
              <>
                <p>The <strong>Synced Lyrics</strong> on the left are what will appear in the karaoke video.</p>
                <p>The orange highlighted words are <strong>gaps</strong> — sections that couldn&apos;t be matched to the reference lyrics and may contain errors (e.g. mis-heard words which sound similar).</p>
                <p>Use <kbd className="px-1 py-0.5 rounded bg-muted text-[0.65rem] font-mono">N</kbd> and <kbd className="px-1 py-0.5 rounded bg-muted text-[0.65rem] font-mono">P</kbd> to jump between gaps. For each one, click play beside the line and compare what you hear with the reference lyrics on the right, then click a word to edit or delete it.</p>
                <p>Watch out for phantom words like &quot;and&quot; at the start or end of lines — the transcription often adds these.</p>
                <p>Try the <strong>Timeline</strong> view to spot any unusually long words (10+ seconds), which usually indicate a timing problem.</p>
              </>
            )}
            <a
              href="https://www.youtube.com/watch?v=-dI3r7qXo3A"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-amber-500 hover:text-amber-400 transition-colors mt-1"
            >
              <ExternalLink className="h-3 w-3" />
              Watch full tutorial
            </a>
          </div>
          <button
            className="text-muted-foreground hover:text-foreground transition-colors shrink-0"
            onClick={handleDismiss}
            aria-label="Dismiss tip"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      {/* Stats & Handlers (shown when toggled via header button) */}
      {statsVisible && (
        <div className={cn('flex gap-2 overflow-hidden', isMobile ? 'flex-col' : 'flex-row', isMobile ? 'h-auto' : 'h-[140px]')}>
          {/* Handler panel */}
          <Card className={cn('p-2', isMobile ? 'w-full' : 'w-[280px] min-w-[280px]')}>
            <h4 className="text-xs text-muted-foreground mb-1">Correction Handlers</h4>
            <div className="flex-1 overflow-auto flex flex-col">
              {availableHandlers.map((handler: { id: string; name: string; description: string }) => (
                <div key={handler.id} className="mb-1">
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <div className="flex items-center gap-2">
                        <Switch
                          checked={enabledHandlers.has(handler.id)}
                          onCheckedChange={(checked) => onHandlerToggle(handler.id, checked)}
                          disabled={isUpdatingHandlers}
                          className="scale-75"
                        />
                        <Label
                          className="text-xs cursor-pointer hover:text-foreground hover:underline transition-colors"
                          onClick={() => onHandlerClick?.(handler.id)}
                        >
                          {handler.name} ({handlerCounts[handler.id] || 0})
                        </Label>
                      </div>
                    </TooltipTrigger>
                    <TooltipContent>
                      <p>{handler.description}</p>
                    </TooltipContent>
                  </Tooltip>
                </div>
              ))}
            </div>
          </Card>

          {/* Metrics panel */}
          <div className="flex-1 grid grid-cols-3 gap-2 h-full">
            <Card
              className="p-2 cursor-pointer hover:bg-muted/30 transition-colors flex flex-col overflow-hidden h-full"
              onClick={onMetricClick.anchor}
            >
              <div className="flex items-center gap-1 mb-0.5">
                <div className="w-3 h-3 rounded bg-blue-500/25 shrink-0" />
                <span className="text-xs text-muted-foreground truncate">Anchor Sequences</span>
              </div>
              <span className="text-lg font-semibold leading-tight">
                {data.metadata.anchor_sequences_count ?? '-'} ({anchorPercentage}%)
              </span>
              <span className="text-[10px] text-muted-foreground leading-tight">
                Matched sections between transcription and reference
              </span>
              <div className="mt-auto space-y-0 text-[10px]">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Words in Anchors</span>
                  <span>{anchorWordCount}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Multi-source Matches</span>
                  <span>{multiSourceAnchors}</span>
                </div>
              </div>
            </Card>

            <Card
              className="p-2 cursor-pointer hover:bg-muted/30 transition-colors flex flex-col overflow-hidden h-full"
              onClick={onMetricClick.corrected}
            >
              <div className="flex items-center gap-1 mb-0.5">
                <div className="w-3 h-3 rounded bg-green-500/25 shrink-0" />
                <span className="text-xs text-muted-foreground truncate">Corrected Gaps</span>
              </div>
              <span className="text-lg font-semibold text-green-500 leading-tight">
                {correctedGapCount} ({correctedPercentage}%)
              </span>
              <span className="text-[10px] text-muted-foreground leading-tight">
                Successfully corrected sections
              </span>
              <div className="mt-auto space-y-0 text-[10px]">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Words Replaced</span>
                  <span>{replacedCount}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Words Added / Deleted</span>
                  <span>+{addedCount} / -{deletedCount}</span>
                </div>
              </div>
            </Card>

            <Card
              className="p-2 cursor-pointer hover:bg-muted/30 transition-colors flex flex-col overflow-hidden h-full"
              onClick={onMetricClick.uncorrected}
            >
              <div className="flex items-center gap-1 mb-0.5">
                <div className="w-3 h-3 rounded bg-orange-500/25 shrink-0" />
                <span className="text-xs text-muted-foreground truncate">Uncorrected Gaps</span>
              </div>
              <span className="text-lg font-semibold text-amber-500 leading-tight">
                {uncorrectedGapCount} ({uncorrectedPercentage}%)
              </span>
              <span className="text-[10px] text-muted-foreground leading-tight">
                Sections that may need manual review
              </span>
              <div className="mt-auto space-y-0 text-[10px]">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Words Uncorrected</span>
                  <span>{uncorrectedWordCount}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Number of Gaps</span>
                  <span>{uncorrectedGaps.length}</span>
                </div>
              </div>
            </Card>
          </div>
        </div>
      )}
    </div>
  )
}
