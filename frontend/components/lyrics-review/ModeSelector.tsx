'use client'

import { useTranslations } from 'next-intl'
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
  const t = useTranslations('lyricsReview.modeSelector')
  return (
    <TooltipProvider>
      <div className="flex items-center gap-1.5 h-8">
        <span className="text-[0.75rem] text-muted-foreground">{t('mode')}</span>
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
                {t('edit')}
              </ToggleGroupItem>
            </TooltipTrigger>
            <TooltipContent className="max-w-xs">
              <p>{t('editDesc')}</p>
            </TooltipContent>
          </Tooltip>

          <Tooltip>
            <TooltipTrigger asChild>
              <ToggleGroupItem
                value="highlight"
                className="h-8 px-2.5 text-[0.75rem] !flex-none aria-checked:font-semibold aria-checked:bg-blue-600 aria-checked:text-white dark:aria-checked:bg-blue-500"
              >
                <Highlighter className="h-3.5 w-3.5 mr-1" />
                {t('highlight')}
              </ToggleGroupItem>
            </TooltipTrigger>
            <TooltipContent className="max-w-xs">
              <p>{t('highlightDesc')}</p>
            </TooltipContent>
          </Tooltip>

          <Tooltip>
            <TooltipTrigger asChild>
              <ToggleGroupItem
                value="delete_word"
                className="h-8 px-2.5 text-[0.75rem] !flex-none aria-checked:font-semibold aria-checked:bg-blue-600 aria-checked:text-white dark:aria-checked:bg-blue-500"
              >
                <Trash2 className="h-3.5 w-3.5 mr-1" />
                {t('delete')}
              </ToggleGroupItem>
            </TooltipTrigger>
            <TooltipContent className="max-w-xs">
              <p>{t('deleteDesc')}</p>
            </TooltipContent>
          </Tooltip>
        </ToggleGroup>
      </div>
    </TooltipProvider>
  )
}
