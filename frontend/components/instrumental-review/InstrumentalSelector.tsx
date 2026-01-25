"use client"

/**
 * Standalone Instrumental Selector (DEPRECATED)
 *
 * This component was used for the separate instrumental selection page in the old
 * two-step review flow. It has been deprecated in favor of the combined review flow
 * where instrumental selection happens alongside lyrics review.
 *
 * For the embedded instrumental selector used in the combined review flow, see
 * InstrumentalSelectorEmbedded.tsx.
 *
 * This component is kept as a stub to avoid breaking exports, but it should not
 * be used for new development. If you need to add standalone instrumental selection
 * (e.g., for finalise-only jobs), consider building a simplified version based on
 * InstrumentalSelectorEmbedded.
 */

import type { Job } from "@/lib/api"

interface InstrumentalSelectorProps {
  job: Job
  isLocalMode?: boolean
}

export function InstrumentalSelector({ job, isLocalMode = false }: InstrumentalSelectorProps) {
  return (
    <div className="flex items-center justify-center min-h-screen">
      <div className="text-center max-w-md p-6">
        <h1 className="text-xl font-semibold mb-2">Page Not Available</h1>
        <p className="text-muted-foreground mb-4">
          Instrumental selection is now part of the combined review flow.
          Please complete your review using the lyrics review page.
        </p>
        {!isLocalMode && (
          <a
            href="/app"
            className="inline-flex items-center justify-center rounded-md text-sm font-medium ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 border border-input bg-background hover:bg-accent hover:text-accent-foreground h-10 px-4 py-2"
          >
            ← Back to dashboard
          </a>
        )}
      </div>
    </div>
  )
}
