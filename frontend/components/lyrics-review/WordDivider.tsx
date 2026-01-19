'use client'

import { Button } from '@/components/ui/button'
import { Plus, Merge, Split } from 'lucide-react'
import { cn } from '@/lib/utils'

interface WordDividerProps {
  onAddWord: () => void
  onMergeWords?: () => void
  onAddSegmentBefore?: () => void
  onAddSegmentAfter?: () => void
  onSplitSegment?: () => void
  onMergeSegment?: () => void
  canMerge?: boolean
  isFirst?: boolean
  isLast?: boolean
  className?: string
}

export default function WordDivider({
  onAddWord,
  onMergeWords,
  onAddSegmentBefore,
  onAddSegmentAfter,
  onSplitSegment,
  onMergeSegment,
  canMerge = false,
  isFirst = false,
  isLast = false,
  className,
}: WordDividerProps) {
  return (
    <div
      className={cn(
        'flex items-center justify-center h-auto min-h-[20px] my-0 w-full overflow-hidden',
        className
      )}
    >
      <div className="flex items-center gap-2 flex-wrap justify-center px-2 z-10">
        <Button
          variant="ghost"
          size="sm"
          onClick={onAddWord}
          title="Add Word"
          className="h-6 px-2 text-primary text-[0.7rem]"
        >
          <Plus className="h-4 w-4 mr-1" />
          Add Word
        </Button>

        {isFirst && onAddSegmentBefore && onMergeSegment && (
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={onAddSegmentBefore}
              title="Add Segment"
              className="h-6 px-2 text-green-500 text-[0.7rem]"
            >
              <Plus className="h-4 w-4 mr-1 rotate-90" />
              Add Segment
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={onMergeSegment}
              title="Merge with Previous Segment"
              className="h-6 px-2 text-yellow-500 text-[0.7rem]"
            >
              <Merge className="h-4 w-4 mr-1 rotate-90" />
              Merge Segment
            </Button>
          </>
        )}

        {onMergeWords && !isLast && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onMergeWords}
            disabled={!canMerge}
            title="Merge Words"
            className="h-6 px-2 text-primary text-[0.7rem] disabled:opacity-50"
          >
            <Merge className="h-4 w-4 mr-1 rotate-90" />
            Merge Words
          </Button>
        )}

        {onSplitSegment && !isLast && (
          <Button
            variant="ghost"
            size="sm"
            onClick={onSplitSegment}
            title="Split Segment"
            className="h-6 px-2 text-yellow-500 text-[0.7rem]"
          >
            <Split className="h-4 w-4 mr-1 rotate-90" />
            Split Segment
          </Button>
        )}

        {isLast && onAddSegmentAfter && onMergeSegment && (
          <>
            <Button
              variant="ghost"
              size="sm"
              onClick={onAddSegmentAfter}
              title="Add Segment"
              className="h-6 px-2 text-green-500 text-[0.7rem]"
            >
              <Plus className="h-4 w-4 mr-1 rotate-90" />
              Add Segment
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={onMergeSegment}
              title="Merge with Next Segment"
              className="h-6 px-2 text-yellow-500 text-[0.7rem]"
            >
              <Merge className="h-4 w-4 mr-1 rotate-90" />
              Merge Segment
            </Button>
          </>
        )}
      </div>
    </div>
  )
}
