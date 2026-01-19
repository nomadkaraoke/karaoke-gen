"use client"

import { Label } from "@/components/ui/label"
import { cn } from "@/lib/utils"
import type { InstrumentalSelectionType, BackingVocalAnalysis, MuteRegion } from "@/lib/api"

interface SelectionOptionsProps {
  value: InstrumentalSelectionType
  onChange: (value: InstrumentalSelectionType) => void
  hasWithBacking: boolean
  hasOriginal: boolean
  hasCustom: boolean
  hasUploaded: boolean
  uploadedFilename?: string
  muteRegionsCount: number
  backingVocalAnalysis?: BackingVocalAnalysis
}

interface SelectionOption {
  value: InstrumentalSelectionType
  label: string
  description: string
  available: boolean
}

export function SelectionOptions({
  value,
  onChange,
  hasWithBacking,
  hasOriginal,
  hasCustom,
  hasUploaded,
  uploadedFilename,
  muteRegionsCount,
  backingVocalAnalysis,
}: SelectionOptionsProps) {
  const options: SelectionOption[] = [
    {
      value: "clean",
      label: "Clean Instrumental",
      description: "No backing vocals",
      available: true,
    },
    {
      value: "with_backing",
      label: "With Backing Vocals",
      description: "All backing vocals included",
      available: hasWithBacking,
    },
    {
      value: "original",
      label: "Original Audio",
      description: "Full original with lead vocals",
      available: hasOriginal,
    },
    {
      value: "custom",
      label: "Custom",
      description: `${muteRegionsCount} region${muteRegionsCount !== 1 ? "s" : ""} muted`,
      available: hasCustom,
    },
    {
      value: "uploaded",
      label: "Uploaded",
      description: uploadedFilename || "Custom instrumental file",
      available: hasUploaded,
    },
  ]

  const availableOptions = options.filter((opt) => opt.available)

  return (
    <div className="flex flex-col gap-2">
      <Label className="text-sm font-semibold">Final Selection</Label>
      <div className="flex flex-col gap-2">
        {availableOptions.map((option) => (
          <button
            key={option.value}
            type="button"
            onClick={() => onChange(option.value)}
            className={cn(
              "flex items-center gap-3 p-3 rounded-lg transition-all text-left",
              "bg-secondary hover:bg-secondary/80",
              "border-2 border-transparent",
              value === option.value && "border-primary bg-primary/10"
            )}
          >
            <div
              className={cn(
                "w-4 h-4 rounded-full border-2 flex items-center justify-center flex-shrink-0",
                value === option.value
                  ? "border-primary"
                  : "border-muted-foreground"
              )}
            >
              {value === option.value && (
                <div className="w-2 h-2 rounded-full bg-primary" />
              )}
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium flex items-center gap-2">
                {option.label}
                {backingVocalAnalysis?.recommended_selection === option.value && (
                  <span className="text-xs px-1.5 py-0.5 bg-green-500/20 text-green-500 rounded">
                    Recommended
                  </span>
                )}
              </div>
              <div className="text-xs text-muted-foreground truncate">
                {option.description}
              </div>
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}
