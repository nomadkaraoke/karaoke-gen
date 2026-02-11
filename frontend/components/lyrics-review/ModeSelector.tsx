'use client'

import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { Highlighter, Pencil, Trash2 } from 'lucide-react'
import { InteractionMode } from '@/lib/lyrics-review/types'

interface ModeSelectorProps {
  effectiveMode: InteractionMode
  onChange: (mode: InteractionMode) => void
}

export default function ModeSelector({ effectiveMode, onChange }: ModeSelectorProps) {
  return (
    <TooltipProvider>
      <div className="flex items-center gap-1.5 h-8">
        <span className="text-[0.75rem] text-muted-foreground">Mode:</span>
        <ToggleGroup
          type="single"
          value={effectiveMode}
          onValueChange={(value) => value && onChange(value as InteractionMode)}
          className="h-8"
        >
          <Tooltip>
            <TooltipTrigger asChild>
              <ToggleGroupItem
                value="edit"
                className="h-8 px-2.5 text-[0.75rem] !flex-none aria-checked:font-semibold aria-checked:bg-blue-600 aria-checked:text-white dark:aria-checked:bg-blue-500"
              >
                <Pencil className="h-3.5 w-3.5 mr-1" />
                Edit
              </ToggleGroupItem>
            </TooltipTrigger>
            <TooltipContent>
              <p>Default mode; click words to edit that lyrics segment</p>
            </TooltipContent>
          </Tooltip>

          <Tooltip>
            <TooltipTrigger asChild>
              <ToggleGroupItem
                value="highlight"
                className="h-8 px-2.5 text-[0.75rem] !flex-none aria-checked:font-semibold aria-checked:bg-blue-600 aria-checked:text-white dark:aria-checked:bg-blue-500"
              >
                <Highlighter className="h-3.5 w-3.5 mr-1" />
                Highlight
              </ToggleGroupItem>
            </TooltipTrigger>
            <TooltipContent>
              <p>Hold SHIFT and click words to highlight the matching anchor sequence</p>
            </TooltipContent>
          </Tooltip>

          <Tooltip>
            <TooltipTrigger asChild>
              <ToggleGroupItem
                value="delete_word"
                className="h-8 px-2.5 text-[0.75rem] !flex-none aria-checked:font-semibold aria-checked:bg-blue-600 aria-checked:text-white dark:aria-checked:bg-blue-500"
              >
                <Trash2 className="h-3.5 w-3.5 mr-1" />
                Delete
              </ToggleGroupItem>
            </TooltipTrigger>
            <TooltipContent>
              <p>Hold CTRL and click words to delete them</p>
            </TooltipContent>
          </Tooltip>
        </ToggleGroup>
      </div>
    </TooltipProvider>
  )
}
