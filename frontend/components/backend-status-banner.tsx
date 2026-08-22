"use client"

import { useEffect, useState } from "react"
import { useTranslations } from "next-intl"
import { Loader2, CloudOff, X } from "lucide-react"
import {
  useBackendStatus,
  __installBackendStatusDevHook,
} from "@/lib/backend-status"

/**
 * App-wide, non-blocking banner that reacts to backend connectivity (see
 * lib/backend-status.ts). During a brief origin blip — e.g. Cloud Run recycling its
 * single min-instance — the API is momentarily unreachable even though nothing is
 * broken and any in-flight karaoke renders keep running. Rather than every screen
 * showing its own scary error, this shows a single reassuring message:
 *
 *   - "reconnecting": a subtle pill with a spinner while we transparently retry.
 *   - "unavailable":  a gentle card explaining it's temporary and that ongoing jobs
 *                     are unaffected, once trouble persists past the threshold.
 *
 * It floats over content (no layout shift) and disappears the instant connectivity
 * is restored.
 */
export function BackendStatusBanner() {
  const status = useBackendStatus()
  const t = useTranslations("backendStatus")
  const [dismissed, setDismissed] = useState(false)

  // Expose the dev/preview trigger (window.__nkBackendStatus) for manual UX review.
  useEffect(() => {
    __installBackendStatusDevHook()
  }, [])

  // Re-arm the dismiss so a *later* outage episode shows again.
  useEffect(() => {
    if (status === "online") setDismissed(false)
  }, [status])

  if (status === "online") return null

  if (status === "reconnecting") {
    return (
      <div
        className="fixed top-3 left-1/2 z-[60] -translate-x-1/2 pointer-events-none"
        role="status"
        aria-live="polite"
      >
        <div className="flex items-center gap-2 rounded-full border border-amber-500/30 bg-amber-950/80 px-3.5 py-1.5 text-xs text-amber-200 shadow-lg backdrop-blur">
          <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
          <span>{t("reconnecting")}</span>
        </div>
      </div>
    )
  }

  // status === "unavailable"
  if (dismissed) return null

  return (
    <div
      className="fixed top-3 left-1/2 z-[60] w-[calc(100%-1.5rem)] max-w-md -translate-x-1/2"
      role="alert"
      aria-live="assertive"
    >
      <div className="relative rounded-xl border border-amber-500/40 bg-amber-950/90 px-4 py-3 pr-9 text-amber-100 shadow-2xl backdrop-blur">
        <button
          type="button"
          onClick={() => setDismissed(true)}
          aria-label={t("dismiss")}
          className="absolute right-2 top-2 rounded-md p-1 text-amber-300/70 transition-colors hover:bg-amber-800/50 hover:text-amber-100"
        >
          <X className="h-4 w-4" />
        </button>
        <div className="flex items-start gap-3">
          <CloudOff className="mt-0.5 h-5 w-5 shrink-0 text-amber-300" />
          <div className="space-y-1">
            <p className="text-sm font-semibold text-amber-50">{t("unavailable.title")}</p>
            <p className="text-xs leading-relaxed text-amber-200/90">{t("unavailable.body")}</p>
            <p className="text-xs leading-relaxed text-amber-200/70">
              {t("unavailable.reassurance")}
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
