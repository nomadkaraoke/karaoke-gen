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
  isActiveGap,
  isUserEdited,
  padding = 'px-[3px] py-[1px]',
  onClick,
  id,
  correction,
}: WordProps) {
  if (/^\s+$/.test(word)) {
    return <>{word}</>
  }

  // Determine background color class
  // User-edited takes priority over gap states (but not currently-playing or anchor)
  const bgColorClass = isCurrentlyPlaying
    ? 'bg-blue-500 text-white'
    : isAnchor
      ? HIGHLIGHT_CLASSES.anchor
      : isUserEdited
        ? HIGHLIGHT_CLASSES.userEdited
        : isCorrectedGap
          ? HIGHLIGHT_CLASSES.corrected
          : isUncorrectedGap
            ? HIGHLIGHT_CLASSES.uncorrectedGap
            : ''

  const wordElement = (
    <span
      id={id}
      className={cn(
        'inline-block mr-[0.25em] transition-colors duration-200 cursor-pointer rounded-sm text-[0.85rem] leading-[1.2]',
        padding,
        bgColorClass,
        shouldFlash && 'animate-lyrics-flash',
        isActiveGap && 'ring-1 ring-yellow-400 dark:ring-yellow-300',
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
