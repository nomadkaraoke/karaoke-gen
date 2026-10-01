'use client'

import { useTranslations } from 'next-intl'
import { AlertTriangle, Play, ListPlus, Info, X, Timer } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { formatGapTime, type OpenMissingLyricsGap } from '@/lib/lyrics-review/utils/missingLyrics'
import type { LyricsSegment } from '@/lib/lyrics-review/types'
import { applyOffsetToTime } from '@/lib/lyrics-review/utils/timingUtils'

interface MissingLyricsCalloutProps {
  gaps: OpenMissingLyricsGap[]
  isReadOnly: boolean
  audioReady?: boolean
  onPlay?: (startTime: number) => void
  onInsert: (gap: OpenMissingLyricsGap) => void
  /** Open a segment in the Edit modal (Tap To Sync) — used when lines are pending sync. */
  onSync?: (segmentIndex: number) => void
  /**
   * Display timing offset (ms), applied exactly like the review's displayData so the range
   * shown — and the time Play receives — match the displayed segments.
   */
  timingOffsetMs?: number
}

/**
 * Non-blocking warning for sung stretches with no lyrics where the reference lyrics
 * expect lines ("evidenced" vocal gaps). Lists the expected lines and offers to insert
 * them as provisional segments. Renders nothing when there are no open gaps.
 */
export default function MissingLyricsCallout({
  gaps,
  isReadOnly,
  audioReady = true,
  onPlay,
  onInsert,
  onSync,
  timingOffsetMs = 0,
}: MissingLyricsCalloutProps) {
  const t = useTranslations('lyricsReview.missingLyrics')
  if (gaps.length === 0) return null

  return (
    <div className="flex flex-col gap-2 mb-2" data-testid="missing-lyrics-callout">
      {gaps.map((open) => {
        const displayStart = applyOffsetToTime(open.gap.start, timingOffsetMs) as number
        const displayEnd = applyOffsetToTime(open.gap.end, timingOffsetMs) as number
        const range = { start: formatGapTime(displayStart), end: formatGapTime(displayEnd) }
        const pending = open.pendingSegmentIndex !== null
        return (
          <div
            key={open.id}
            id={open.id}
            role="status"
            data-testid="missing-lyrics-gap"
            className="flex items-start gap-2 rounded-md border border-amber-500/50 bg-amber-500/10 p-3 text-sm"
          >
            <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0 text-amber-500" />
            <div className="flex-1 min-w-0">
              <p className="font-medium text-amber-500">{t('title', range)}</p>
              <p className="text-muted-foreground">{t('description')}</p>
              {open.lines.length > 0 ? (
                <>
                  <p className="mt-2 text-xs text-muted-foreground">
                    {t('expectedFrom', { source: open.source ?? '' })}
                  </p>
                  <ul className="mt-1 space-y-0.5" data-testid="missing-lyrics-lines">
                    {open.lines.map((line, i) => (
                      <li key={i} dir="auto" className="pl-2 border-l-2 border-amber-500/50">
                        {line}
                      </li>
                    ))}
                  </ul>
                </>
              ) : (
                <p className="mt-2 text-xs text-muted-foreground">{t('noLines')}</p>
              )}
              {pending && (
                <p className="mt-2 text-xs text-amber-500" data-testid="missing-lyrics-pending">
                  {t('pendingSync')}
                </p>
              )}
              <div className="mt-2 flex flex-wrap gap-2">
                {onPlay && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 text-xs"
                    onClick={() => onPlay(displayStart)}
                    disabled={!audioReady}
                  >
                    <Play className="h-3.5 w-3.5 mr-1" />
                    {t('play')}
                  </Button>
                )}
                {pending && onSync && !isReadOnly && (
                  <Button
                    variant="outline"
                    size="sm"
                    className="h-7 text-xs"
                    onClick={() => onSync(open.pendingSegmentIndex as number)}
                    data-testid="missing-lyrics-pending-sync"
                  >
                    <Timer className="h-3.5 w-3.5 mr-1" />
                    {t('syncTiming')}
                  </Button>
                )}
                {!pending && open.lines.length > 0 && (
                  // Disabled buttons get pointer-events-none, so the read-only explanation
                  // lives on a focusable wrapper where hover/focus can reach it.
                  <span
                    title={isReadOnly ? t('readOnly') : undefined}
                    tabIndex={isReadOnly ? 0 : undefined}
                    className="inline-flex"
                    data-testid="missing-lyrics-insert-wrapper"
                  >
                    <Button
                      size="sm"
                      className="h-7 text-xs"
                      onClick={() => onInsert(open)}
                      disabled={isReadOnly}
                      aria-label={isReadOnly ? `${t('insert')} (${t('readOnly')})` : undefined}
                      data-testid="missing-lyrics-insert"
                    >
                      <ListPlus className="h-3.5 w-3.5 mr-1" />
                      {t('insert')}
                    </Button>
                  </span>
                )}
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}

interface MissingLyricsResyncHintProps {
  /** The inserted lines still present in the current segments, with their indices. */
  lines: { segment: LyricsSegment; index: number }[]
  /** Open the existing Edit modal (with Tap To Sync) for the segment at `index`. */
  onSync: (index: number) => void
  onDismiss: () => void
}

/**
 * Shown after "Insert these lines": the inserted words only have placeholder timing,
 * so guide the reviewer to the existing per-line Tap To Sync in the Edit modal.
 */
export function MissingLyricsResyncHint({ lines, onSync, onDismiss }: MissingLyricsResyncHintProps) {
  const t = useTranslations('lyricsReview.missingLyrics')
  if (lines.length === 0) return null

  return (
    <div
      role="status"
      data-testid="missing-lyrics-resync-hint"
      className="mb-2 flex items-start gap-2 rounded-md border border-primary/40 bg-primary/10 p-3 text-sm"
    >
      <Info className="h-4 w-4 mt-0.5 shrink-0 text-primary" />
      <div className="flex-1 min-w-0">
        <p className="font-medium">{t('insertedTitle')}</p>
        <p className="text-muted-foreground">{t('insertedHint')}</p>
        <ul className="mt-2 space-y-1">
          {lines.map(({ segment, index }) => (
            <li key={segment.id} className="flex items-center gap-2">
              <Button
                variant="outline"
                size="sm"
                className="h-7 text-xs shrink-0"
                onClick={() => onSync(index)}
                data-testid="missing-lyrics-sync-line"
              >
                <Timer className="h-3.5 w-3.5 mr-1" />
                {t('syncTiming')}
              </Button>
              <span dir="auto" className="truncate">{segment.text}</span>
            </li>
          ))}
        </ul>
      </div>
      <Button
        variant="ghost"
        size="icon"
        className="h-6 w-6 shrink-0"
        onClick={onDismiss}
        aria-label={t('dismiss')}
        title={t('dismiss')}
      >
        <X className="h-3.5 w-3.5" />
      </Button>
    </div>
  )
}
