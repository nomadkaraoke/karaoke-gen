"use client"

import { Button } from "@/components/ui/button"
import { X } from "lucide-react"
import type { MuteRegion, AudibleSegment } from "@/lib/api"

interface MuteRegionEditorProps {
  regions: MuteRegion[]
  audibleSegments: AudibleSegment[]
  hasCustom: boolean
  isCreatingCustom: boolean
  onRemoveRegion: (index: number) => void
  onClearAll: () => void
  onCreateCustom: () => void
  onSeekTo: (time: number) => void
  onAddSegment: (index: number) => void
}

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds)) return "0:00"
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins}:${secs.toString().padStart(2, "0")}`
}

export function MuteRegionEditor({
  regions,
  audibleSegments,
  hasCustom,
  isCreatingCustom,
  onRemoveRegion,
  onClearAll,
  onCreateCustom,
  onSeekTo,
  onAddSegment,
}: MuteRegionEditorProps) {
  const hasRegions = regions.length > 0
  const hasSegments = audibleSegments.length > 0

  return (
    <div className="flex-1 bg-card border border-border rounded-lg p-3 flex flex-col gap-2 max-h-[140px]">
      {/* Header */}
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold">
          Mute Regions {hasRegions && `(${regions.length})`}
        </span>
        {hasRegions && (
          <div className="flex gap-1.5">
            <Button
              variant="secondary"
              size="sm"
              onClick={onClearAll}
              className="h-7 text-xs"
            >
              Clear
            </Button>
            {!hasCustom && (
              <Button
                size="sm"
                onClick={onCreateCustom}
                disabled={isCreatingCustom}
                className="h-7 text-xs"
              >
                {isCreatingCustom ? "Creating..." : "Create Custom"}
              </Button>
            )}
          </div>
        )}
      </div>

      {/* Region tags */}
      {hasRegions ? (
        <div className="flex flex-wrap gap-1.5 overflow-y-auto flex-1">
          {regions.map((region, index) => (
            <div
              key={index}
              className="inline-flex items-center gap-1 px-2 py-1 bg-secondary rounded text-xs"
            >
              <button
                type="button"
                onClick={() => onSeekTo(region.start_seconds)}
                className="hover:text-primary cursor-pointer"
              >
                {formatTime(region.start_seconds)} - {formatTime(region.end_seconds)}
              </button>
              <button
                type="button"
                onClick={() => onRemoveRegion(index)}
                className="text-muted-foreground hover:text-destructive ml-1"
              >
                <X className="w-3 h-3" />
              </button>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-xs text-muted-foreground">
          {hasSegments
            ? "Click segments below or Shift + drag on waveform"
            : "No backing vocals detected - clean instrumental recommended"}
        </div>
      )}

      {/* Quick segment buttons */}
      {hasSegments && (
        <div className="flex flex-wrap gap-1">
          {audibleSegments.slice(0, 8).map((segment, index) => (
            <button
              key={index}
              type="button"
              onClick={() => onAddSegment(index)}
              title="Add to mute regions"
              className="px-1.5 py-0.5 bg-background border border-border rounded text-[10px] text-muted-foreground hover:border-primary hover:text-primary transition-colors"
            >
              {formatTime(segment.start_seconds)} - {formatTime(segment.end_seconds)}
            </button>
          ))}
          {audibleSegments.length > 8 && (
            <span className="text-xs text-muted-foreground px-1 py-0.5">
              +{audibleSegments.length - 8} more
            </span>
          )}
        </div>
      )}
    </div>
  )
}
