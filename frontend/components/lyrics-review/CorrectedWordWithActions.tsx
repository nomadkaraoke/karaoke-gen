'use client'

import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { COLORS } from '@/lib/lyrics-review/constants'
import { cn } from '@/lib/utils'
import { Undo, Edit, CheckCircle } from 'lucide-react'

interface CorrectionInfo {
  originalWord: string
  handler: string
  confidence: number
  source: string
  reason?: string
}

interface CorrectedWordWithActionsProps {
  word: string
  originalWord: string
  correction: CorrectionInfo
  onRevert: () => void
  onEdit: () => void
  onAccept: () => void
  onClick?: () => void
  backgroundColor?: string
  shouldFlash?: boolean
  showActions?: boolean
}

export default function CorrectedWordWithActions({
  word,
  originalWord,
  onRevert,
  onEdit,
  onAccept,
  onClick,
  backgroundColor,
  shouldFlash,
  showActions = true,
}: CorrectedWordWithActionsProps) {
  const handleAction = (e: React.MouseEvent, action: () => void) => {
    e.stopPropagation()
    action()
  }

  return (
    <span
      className={cn(
        'inline-flex items-center gap-0.5 px-[3px] py-[1px] rounded-sm cursor-pointer relative',
        'hover:bg-green-500/35',
        shouldFlash && 'animate-pulse'
      )}
      style={{ backgroundColor: backgroundColor || COLORS.corrected }}
      onClick={onClick}
    >
      {/* Original word label - shown above */}
      <span className="absolute -top-3.5 left-0 text-[0.6rem] text-muted-foreground line-through opacity-70 whitespace-nowrap pointer-events-none">
        {originalWord}
      </span>

      {/* Current word */}
      <span className="text-[0.85rem] leading-[1.2] font-semibold">{word}</span>

      {/* Action buttons */}
      {showActions && (
        <span className="inline-flex items-center gap-[1px] ml-0.5">
          <TooltipProvider delayDuration={200}>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  size="icon"
                  variant="ghost"
                  className="h-5 w-5 min-h-0 min-w-0 p-0.5 bg-slate-800/90 dark:bg-slate-800/90 border border-border hover:bg-slate-700 dark:hover:bg-slate-700 hover:scale-110 transition-transform"
                  onClick={(e) => handleAction(e, onRevert)}
                  aria-label="revert correction"
                >
                  <Undo className="h-3.5 w-3.5" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="top">Revert to original</TooltipContent>
            </Tooltip>

            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  size="icon"
                  variant="ghost"
                  className="h-5 w-5 min-h-0 min-w-0 p-0.5 bg-slate-800/90 dark:bg-slate-800/90 border border-border hover:bg-slate-700 dark:hover:bg-slate-700 hover:scale-110 transition-transform"
                  onClick={(e) => handleAction(e, onEdit)}
                  aria-label="edit correction"
                >
                  <Edit className="h-3.5 w-3.5" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="top">Edit correction</TooltipContent>
            </Tooltip>

            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  size="icon"
                  variant="ghost"
                  className="h-5 w-5 min-h-0 min-w-0 p-0.5 bg-slate-800/90 dark:bg-slate-800/90 border border-border hover:bg-slate-700 dark:hover:bg-slate-700 hover:scale-110 transition-transform text-green-500 hidden sm:flex"
                  onClick={(e) => handleAction(e, onAccept)}
                  aria-label="accept correction"
                >
                  <CheckCircle className="h-3.5 w-3.5" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="top">Accept correction</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        </span>
      )}
    </span>
  )
}
