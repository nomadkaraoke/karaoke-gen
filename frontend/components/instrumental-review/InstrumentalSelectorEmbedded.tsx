"use client"

import { useState, useCallback, useMemo } from "react"
import { useTranslations } from 'next-intl'
import { Card } from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Play, Pause, Music } from "lucide-react"
import { cn } from "@/lib/utils"
import type { InstrumentalSelectionType } from "@/lib/api"

export interface InstrumentalOption {
  id: InstrumentalSelectionType
  label: string
  audio_url: string | null
}

/** Analysis data for backing vocals - flexible type to match correction-data endpoint */
interface BackingVocalsAnalysisProps {
  audible_segments?: Array<{ start_seconds: number; end_seconds: number }>
  audible_percentage?: number
  recommended_selection?: string
  has_audible_content?: boolean
  total_duration_seconds?: number
  total_audible_duration_seconds?: number
  silence_threshold_db?: number
}

interface InstrumentalSelectorEmbeddedProps {
  /** Available instrumental options from the correction-data endpoint */
  options: InstrumentalOption[]
  /** Backing vocals analysis data from the correction-data endpoint */
  analysis?: BackingVocalsAnalysisProps | null
  /** Currently selected instrumental option */
  value: InstrumentalSelectionType | null
  /** Callback when selection changes */
  onChange: (value: InstrumentalSelectionType) => void
  /** Whether to show compact layout (for embedding in review modal) */
  compact?: boolean
  /** Disabled state */
  disabled?: boolean
}

export function InstrumentalSelectorEmbedded({
  options,
  analysis,
  value,
  onChange,
  compact = false,
  disabled = false,
}: InstrumentalSelectorEmbeddedProps) {
  const t = useTranslations('instrumentalReview')
  const [playingId, setPlayingId] = useState<string | null>(null)
  const [audioElement] = useState<HTMLAudioElement | null>(() =>
    typeof window !== 'undefined' ? new Audio() : null
  )

  // Get recommendation from analysis
  const recommendedOption = useMemo(() => {
    if (!analysis) return null
    return analysis.recommended_selection || null
  }, [analysis])

  // Handle audio playback
  const handlePlayPause = useCallback((optionId: string, audioUrl: string | null) => {
    if (!audioElement || !audioUrl) return

    if (playingId === optionId) {
      // Stop playing
      audioElement.pause()
      audioElement.currentTime = 0
      setPlayingId(null)
    } else {
      // Start playing new audio
      audioElement.src = audioUrl
      audioElement.play().catch(() => {})
      setPlayingId(optionId)

      // Stop when ended
      audioElement.onended = () => setPlayingId(null)
    }
  }, [audioElement, playingId])

  // Clean up audio on unmount
  const handleSelect = useCallback((optionId: InstrumentalSelectionType) => {
    if (disabled) return
    onChange(optionId)
  }, [onChange, disabled])

  if (options.length === 0) {
    return (
      <Card className="p-4">
        <div className="text-sm text-muted-foreground">
          {t('noOptionsAvailable')}
        </div>
      </Card>
    )
  }

  return (
    <Card className={cn("p-4", compact && "p-3")}>
      <div className="flex items-center gap-2 mb-3">
        <Music className="h-4 w-4 text-muted-foreground" />
        <Label className={cn("font-semibold", compact ? "text-sm" : "text-base")}>
          {t('selectTrack')}
        </Label>
        {!value && (
          <Badge variant="destructive" className="ml-auto text-xs">
            {t('required')}
          </Badge>
        )}
      </div>

      {/* Backing vocals info */}
      {analysis && (analysis.audible_percentage ?? 0) > 0 && (
        <div className="mb-3 text-xs text-muted-foreground">
          <span className="font-medium">
            {t('backingVocalsDetected', { percentage: (analysis.audible_percentage ?? 0).toFixed(0) })}
          </span>
          {(analysis.audible_segments?.length ?? 0) > 0 && (
            <span> {t('segmentsCount', { count: analysis.audible_segments?.length ?? 0 })}</span>
          )}
        </div>
      )}

      <div className={cn("flex flex-col gap-2", compact && "gap-1.5")}>
        {options.map((option) => {
          const isSelected = value === option.id
          const isRecommended = recommendedOption === option.id
          const isPlaying = playingId === option.id

          return (
            <div
              key={option.id}
              className={cn(
                "flex items-center gap-3 p-3 rounded-lg transition-all cursor-pointer",
                "bg-secondary/50 hover:bg-secondary/80",
                "border-2",
                isSelected ? "border-primary bg-primary/10" : "border-transparent",
                disabled && "opacity-50 cursor-not-allowed",
                compact && "p-2"
              )}
              onClick={() => handleSelect(option.id)}
              role="button"
              aria-pressed={isSelected}
              tabIndex={disabled ? -1 : 0}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault()
                  handleSelect(option.id)
                }
              }}
            >
              {/* Radio indicator */}
              <div
                className={cn(
                  "w-4 h-4 rounded-full border-2 flex items-center justify-center flex-shrink-0",
                  isSelected ? "border-primary" : "border-muted-foreground"
                )}
              >
                {isSelected && (
                  <div className="w-2 h-2 rounded-full bg-primary" />
                )}
              </div>

              {/* Label and description */}
              <div className="flex-1 min-w-0">
                <div className={cn("font-medium flex items-center gap-2", compact ? "text-sm" : "text-sm")}>
                  {option.label}
                  {isRecommended && (
                    <Badge variant="secondary" className="text-xs bg-green-500/20 text-green-600">
                      {t('options.recommended')}
                    </Badge>
                  )}
                </div>
                <div className={cn("text-muted-foreground", compact ? "text-xs" : "text-xs")}>
                  {option.id === 'clean' ? t('cleanDescription') : option.id === 'with_backing' ? t('backingVocalsIncluded') : option.label}
                </div>
              </div>

              {/* Preview button */}
              {option.audio_url && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-8 w-8 flex-shrink-0"
                  onClick={(e) => {
                    e.stopPropagation()
                    handlePlayPause(option.id, option.audio_url)
                  }}
                  disabled={disabled}
                >
                  {isPlaying ? (
                    <Pause className="h-4 w-4" />
                  ) : (
                    <Play className="h-4 w-4" />
                  )}
                </Button>
              )}
            </div>
          )
        })}
      </div>
    </Card>
  )
}

export default InstrumentalSelectorEmbedded
