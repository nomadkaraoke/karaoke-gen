'use client'

import React from 'react'
import { useTranslations } from 'next-intl'
import { Clock } from 'lucide-react'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { COLORS, HIGHLIGHT_CLASSES } from '@/lib/lyrics-review/constants'
import { WordProps } from '@/lib/lyrics-review/types'
import { cn } from '@/lib/utils'

export const WordComponent = React.memo(function Word({
  word,
  shouldFlash,
  isAnchor,
  isCorrectedGap,
  isUncorrectedGap,
  isCurrentlyPlaying,
  isActiveGap,
  isUserEdited,
  isAiCorrected,
  aiOriginalText,
  aiTimingEstimated,
  longWordSeconds,
  timelineGrow,
  padding = 'px-[3px] py-[1px]',
  onClick,
  id,
  correction,
}: WordProps) {
  const t = useTranslations('lyricsReview.transcription')
  if (/^\s+$/.test(word)) {
    return <>{word}</>
  }

  // Determine background color class. Currently-playing (blue) always wins so
  // the karaoke highlight stays legible; then AI-corrected (purple) so it reads
  // distinctly from human edits (lime) and pipeline corrections (green).
  const bgColorClass = isCurrentlyPlaying
    ? 'bg-blue-500 text-white'
    : isAiCorrected
      ? HIGHLIGHT_CLASSES.aiCorrected
      : isAnchor
        ? HIGHLIGHT_CLASSES.anchor
        : isUserEdited
          ? HIGHLIGHT_CLASSES.userEdited
          : isCorrectedGap
            ? HIGHLIGHT_CLASSES.corrected
            : isUncorrectedGap
              ? HIGHLIGHT_CLASSES.uncorrectedGap
              : ''

  // Estimated-timing AI words (a word split into several, or an inserted word)
  // almost always need a timing nudge — flag them with a dashed amber underline
  // so they stand out from trustworthy 1:1 replacements.
  const estimatedMarker =
    isAiCorrected && aiTimingEstimated
      ? 'underline decoration-dashed decoration-2 decoration-amber-500 underline-offset-[3px]'
      : ''

  // Advanced (timeline) layout: the pill is a flex item whose grow weight is its
  // duration, so a segment's words fill the full width proportional to how long
  // each lasts (the parent renders the flex row and the gap spacers).
  const isTimeline = timelineGrow != null
  // A long-word pill becomes a flex row so the duration badge can hug the right
  // edge (ml-auto). Plain pills keep block + ellipsis for truncation.
  const hasDurationBadge = longWordSeconds != null
  const pillStyle: React.CSSProperties | undefined = isTimeline
    ? {
        display: hasDurationBadge ? 'flex' : 'block',
        alignItems: hasDurationBadge ? 'center' : undefined,
        width: '100%',
        boxSizing: 'border-box',
        ...(hasDurationBadge
          ? {}
          : { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }),
      }
    : undefined
  // flexBasis 'auto' keeps each word at least as wide as its text (readable);
  // flexGrow (= duration) shares the remaining width so longer words are wider
  // and the line fills the section. Truncation only kicks in on dense lines.
  const layoutStyle: React.CSSProperties | undefined = isTimeline
    ? { flexGrow: Math.max(timelineGrow, 0.05), flexShrink: 1, flexBasis: 'auto', minWidth: 0 }
    : undefined

  const pill = (
    <span
      id={id}
      className={cn(
        hasDurationBadge ? 'inline-flex items-center gap-1' : 'inline-block',
        'transition-colors duration-200 cursor-pointer rounded-sm text-[0.85rem] leading-[1.2]',
        !isTimeline && 'mr-[0.25em]',
        padding,
        bgColorClass,
        shouldFlash && 'animate-lyrics-flash',
        isActiveGap && 'ring-1 ring-yellow-400 dark:ring-yellow-300',
        correction && 'underline decoration-dotted underline-offset-2 decoration-muted-foreground',
        estimatedMarker,
        'hover:bg-foreground/[0.08]'
      )}
      style={pillStyle}
      onClick={onClick}
    >
      {word}
      {/* Long-word duration sits INSIDE the pill — long words have wide pills
          anyway, and it avoids an extra info-row above the line. */}
      {longWordSeconds != null && (
        <span
          title={t('longWordWarning', { seconds: longWordSeconds.toFixed(1) })}
          className="ml-auto pl-1 shrink-0 inline-flex items-center gap-0.5 align-middle whitespace-nowrap rounded-[3px] border border-amber-500/50 bg-amber-500/20 px-1 text-[0.6rem] leading-none text-amber-600 dark:text-amber-300"
        >
          <Clock className="h-2.5 w-2.5" />
          {longWordSeconds.toFixed(1)}s
        </span>
      )}
    </span>
  )

  // The grey "ghost" of the original transcription floats ABOVE AI-corrected
  // words; it needs a relative wrapper with visible overflow.
  const aiBubble = isAiCorrected && aiOriginalText && (
    <span className="absolute left-0 bottom-full mb-[1px] z-10 whitespace-nowrap rounded border border-dashed border-muted-foreground/50 bg-background/90 px-1 text-[0.6rem] leading-tight text-muted-foreground line-through pointer-events-none">
      {aiOriginalText}
    </span>
  )
  const hasAbove = Boolean(aiBubble)

  const correctionBody = correction && (
    <div className="text-xs space-y-0.5">
      <div>
        <strong>Original:</strong> &quot;{correction.originalWord}&quot;
      </div>
      <div>
        <strong>Corrected by:</strong> {correction.handler}
      </div>
      <div>
        <strong>Source:</strong> {correction.source}
      </div>
      {correction.reason && (
        <div>
          <strong>Reason:</strong> {correction.reason}
        </div>
      )}
      {correction.confidence !== undefined && correction.confidence > 0 && (
        <div>
          <strong>Confidence:</strong> {(correction.confidence * 100).toFixed(0)}%
        </div>
      )}
    </div>
  )

  // Common case: a bare pill (optionally with the legacy correction tooltip).
  // AI-corrected words always fall through so they get their explanatory
  // tooltip, even when they have no original-text bubble (split/inserted words).
  if (!hasAbove && !isTimeline && !isAiCorrected) {
    if (correction) {
      return (
        <TooltipProvider delayDuration={200}>
          <Tooltip>
            <TooltipTrigger asChild>{pill}</TooltipTrigger>
            <TooltipContent side="top" className="max-w-xs">
              {correctionBody}
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      )
    }
    return pill
  }

  const layoutItem = (
    <span className="relative inline-block align-bottom" style={layoutStyle}>
      {aiBubble}
      {pill}
    </span>
  )

  // AI-corrected words get an explanatory tooltip on the whole pill.
  if (isAiCorrected) {
    const aiTooltip = aiOriginalText
      ? aiTimingEstimated
        ? t('aiTooltipReplacedEstimated', { original: aiOriginalText })
        : t('aiTooltipReplaced', { original: aiOriginalText })
      : t('aiTooltipEstimated')
    return (
      <TooltipProvider delayDuration={200}>
        <Tooltip>
          <TooltipTrigger asChild>{layoutItem}</TooltipTrigger>
          <TooltipContent side="top" className="max-w-xs text-xs">
            {aiTooltip}
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    )
  }

  if (correction) {
    return (
      <TooltipProvider delayDuration={200}>
        <Tooltip>
          <TooltipTrigger asChild>{layoutItem}</TooltipTrigger>
          <TooltipContent side="top" className="max-w-xs">
            {correctionBody}
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    )
  }

  return layoutItem
})
