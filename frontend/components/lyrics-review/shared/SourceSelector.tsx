'use client'

import { Button } from '@/components/ui/button'
import { Plus } from 'lucide-react'

export interface SourceSelectorProps {
  currentSource: string
  onSourceChange: (source: string) => void
  availableSources: string[]
  onAddLyrics?: () => void
}

export function SourceSelector({
  currentSource,
  onSourceChange,
  availableSources,
  onAddLyrics,
}: SourceSelectorProps) {
  return (
    <div className="flex flex-wrap gap-1 items-center">
      {availableSources.map((source) => (
        <Button
          key={source}
          size="sm"
          variant={currentSource === source ? 'default' : 'outline'}
          onClick={() => onSourceChange(source)}
          className="h-6 px-2 text-[0.7rem] leading-tight min-w-0"
        >
          {/* Capitalize first letter of source */}
          {source.charAt(0).toUpperCase() + source.slice(1)}
        </Button>
      ))}
      {onAddLyrics && (
        <Button
          size="sm"
          variant="outline"
          onClick={onAddLyrics}
          className="h-6 px-2 text-[0.7rem] leading-tight min-w-0"
        >
          <Plus className="h-3 w-3 mr-0.5" />
          New
        </Button>
      )}
    </div>
  )
}
