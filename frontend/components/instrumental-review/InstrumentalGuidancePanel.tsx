"use client"

import { useState } from "react"
import { useTranslations } from 'next-intl'
import { ExternalLink, Lightbulb, X } from "lucide-react"
import type { BackingVocalAnalysis, AudibleSegment } from "@/lib/api"

interface InstrumentalGuidancePanelProps {
  analysis: BackingVocalAnalysis
  audibleSegments: AudibleSegment[]
}

export function InstrumentalGuidancePanel({
  analysis,
  audibleSegments,
}: InstrumentalGuidancePanelProps) {
  const t = useTranslations('instrumentalReview.guidance')
  const [guidanceDismissed, setGuidanceDismissed] = useState(() => {
    if (typeof window === "undefined") return false
    return localStorage.getItem("instrumentalReviewGuidanceDismissed") === "true"
  })

  const handleDismiss = () => {
    setGuidanceDismissed(true)
    if (typeof window !== "undefined") {
      localStorage.setItem("instrumentalReviewGuidanceDismissed", "true")
    }
  }

  const handleShowTips = () => {
    setGuidanceDismissed(false)
    if (typeof window !== "undefined") {
      localStorage.removeItem("instrumentalReviewGuidanceDismissed")
    }
  }

  const isCleanRecommended = analysis.recommended_selection === "clean"
  const percentage = analysis.audible_percentage
  const hasSegments = audibleSegments.length > 0

  if (guidanceDismissed) {
    return (
      <div className="flex items-center gap-2 text-xs">
        <button
          className="text-amber-500 hover:text-amber-400 transition-colors"
          onClick={handleShowTips}
        >
          {t('showTips')}
        </button>
      </div>
    )
  }

  // Simple case: clean recommended with low backing vocals
  if (isCleanRecommended && percentage < 10) {
    return (
      <div className="flex items-start gap-2 p-2.5 rounded-md bg-amber-500/10 border border-amber-500/20">
        <Lightbulb className="h-4 w-4 text-amber-500 shrink-0 mt-0.5" />
        <div className="text-xs text-muted-foreground flex-1 leading-relaxed space-y-1.5">
          <p>
            <strong>{t('chooseBackingTrack')}</strong>
          </p>
          <p>
            {t('littleBackingVocals', { percentage: percentage.toFixed(0) })}
          </p>
          <p className="text-muted-foreground/70">
            {t('hearDifference')}
          </p>
          <a
            href="https://www.youtube.com/watch?v=-dI3r7qXo3A"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-amber-500 hover:text-amber-400 transition-colors mt-1"
          >
            <ExternalLink className="h-3 w-3" />
            {t('watchFullTutorial')}
          </a>
        </div>
        <button
          className="text-muted-foreground hover:text-foreground transition-colors shrink-0"
          onClick={handleDismiss}
          aria-label={t('dismissTip')}
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    )
  }

  // Complex case: backing vocals present, needs review
  return (
    <div className="flex items-start gap-2 p-2.5 rounded-md bg-amber-500/10 border border-amber-500/20">
      <Lightbulb className="h-4 w-4 text-amber-500 shrink-0 mt-0.5" />
      <div className="text-xs text-muted-foreground flex-1 leading-relaxed space-y-1.5">
        <p>
          <strong>{t('chooseBackingTrack')}</strong>
        </p>
        <p>
          {t('backingVocalsExplanation')}
        </p>
        <div className="space-y-1 pl-2 border-l-2 border-amber-500/30">
          <p>
            {t('step1', { segments: hasSegments ? ` (${audibleSegments.length} detected)` : '' })}
          </p>
          <p>
            {t('step2')}
          </p>
          <p>
            {t('step3')}
          </p>
        </div>
        <a
          href="https://www.youtube.com/watch?v=-dI3r7qXo3A"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-amber-500 hover:text-amber-400 transition-colors mt-1"
        >
          <ExternalLink className="h-3 w-3" />
          {t('watchFullTutorial')}
        </a>
      </div>
      <button
        className="text-muted-foreground hover:text-foreground transition-colors shrink-0"
        onClick={handleDismiss}
        aria-label={t('dismissTip')}
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  )
}
