"use client"

import { useState } from "react"
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
          Show tips
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
            <strong>Choose your karaoke backing track.</strong> The AI separated
            the vocals from the music — now pick which version sounds best.
          </p>
          <p>
            This track has very little backing vocal content ({percentage.toFixed(0)}%).
            We recommend the <strong>Clean Instrumental</strong> — just confirm
            below to proceed.
          </p>
          <p className="text-muted-foreground/70">
            Want to hear the difference? Use the audio buttons above to compare,
            or click the{" "}
            <span className="inline-block w-2 h-2 rounded-full bg-pink-500/60 align-middle" />{" "}
            pink sections in the waveform to listen to the backing vocals.
          </p>
          <a
            href="https://www.youtube.com/watch?v=-dI3r7qXo3A"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-amber-500 hover:text-amber-400 transition-colors mt-1"
          >
            <ExternalLink className="h-3 w-3" />
            Watch full tutorial
          </a>
        </div>
        <button
          className="text-muted-foreground hover:text-foreground transition-colors shrink-0"
          onClick={handleDismiss}
          aria-label="Dismiss tip"
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
          <strong>Choose your karaoke backing track.</strong> The AI separated
          the vocals from the music — now pick which version sounds best.
        </p>
        <p>
          <strong>Backing vocals</strong> are the harmony/chorus voices that sing
          along with the lead. Some karaoke singers prefer to keep them for guidance,
          others prefer a clean instrumental.
        </p>
        <div className="space-y-1 pl-2 border-l-2 border-amber-500/30">
          <p>
            <strong>Step 1:</strong> Click the{" "}
            <span className="inline-block w-2 h-2 rounded-full bg-pink-500/60 align-middle" />{" "}
            pink sections in the waveform to hear the backing vocals
            {hasSegments && ` (${audibleSegments.length} detected)`}.
          </p>
          <p>
            <strong>Step 2:</strong> Decide — keep them or use the clean
            instrumental? Use the audio toggle buttons above to compare.
          </p>
          <p>
            <strong>Step 3</strong> (if needed): If you want backing vocals but
            some sections have unwanted lead vocal bleed,{" "}
            <kbd className="px-1 py-0.5 rounded bg-muted text-[0.65rem] font-mono">
              Shift
            </kbd>
            +drag on the waveform to mark those sections, then click{" "}
            <strong>Create Custom</strong>.
          </p>
        </div>
        <a
          href="https://www.youtube.com/watch?v=-dI3r7qXo3A"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-amber-500 hover:text-amber-400 transition-colors mt-1"
        >
          <ExternalLink className="h-3 w-3" />
          Watch full tutorial
        </a>
      </div>
      <button
        className="text-muted-foreground hover:text-foreground transition-colors shrink-0"
        onClick={handleDismiss}
        aria-label="Dismiss tip"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  )
}
