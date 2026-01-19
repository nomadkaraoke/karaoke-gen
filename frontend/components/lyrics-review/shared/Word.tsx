'use client'

import React from 'react'
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
  padding = 'px-[3px] py-[1px]',
  onClick,
  correction,
}: WordProps) {
  if (/^\s+$/.test(word)) {
    return <>{word}</>
  }

  // Determine background color class
  const bgColorClass = isCurrentlyPlaying
    ? 'bg-blue-500 text-white'
    : isAnchor
      ? HIGHLIGHT_CLASSES.anchor
      : isCorrectedGap
        ? HIGHLIGHT_CLASSES.corrected
        : isUncorrectedGap
          ? HIGHLIGHT_CLASSES.uncorrectedGap
          : ''

  const wordElement = (
    <span
      className={cn(
        'inline-block mr-[0.25em] transition-colors duration-200 cursor-pointer rounded-sm text-[0.85rem] leading-[1.2]',
        padding,
        bgColorClass,
        shouldFlash && 'animate-lyrics-flash',
        correction && 'underline decoration-dotted underline-offset-2 decoration-muted-foreground',
        'hover:bg-foreground/[0.08]'
      )}
      onClick={onClick}
    >
      {word}
    </span>
  )

  if (correction) {
    return (
      <TooltipProvider delayDuration={200}>
        <Tooltip>
          <TooltipTrigger asChild>{wordElement}</TooltipTrigger>
          <TooltipContent side="top" className="max-w-xs">
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
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    )
  }

  return wordElement
})
