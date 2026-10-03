"use client"

import { useEffect, useRef, useState } from "react"
import { useTranslations } from "next-intl"
import { Loader2, CloudOff, X } from "lucide-react"
import {
  useBackendStatus,
  getBackendStatusDebug,
  installBackendPrewarm,
  __installBackendStatusDevHook,
  type BackendStatus,
} from "@/lib/backend-status"
import {
  reportDegradationEvent,
  startDegradationEpisode,
  endDegradationEpisode,
} from "@/lib/degradation-events"
import { __backendPrewarm } from "@/lib/api"

/**
 * App-wide, non-blocking banner that reacts to backend connectivity (see
 * lib/backend-status.ts). During a brief origin blip — e.g. Cloud Run recycling its
 * single min-instance — the API is momentarily unreachable even though nothing is
 * broken and any in-flight karaoke renders keep running. Rather than every screen
 * showing its own scary error, this shows a single reassuring message:
 *
 *   - "waking":       a calm, branded "starting up our karaoke servers" card while a
 *                     scaled-to-zero backend cold-starts (~15-20s). No warning colours.
 *   - "reconnecting": a subtle pill with a spinner while we transparently retry.
 *   - "unavailable":  a gentle card explaining it's temporary and that ongoing jobs
 *                     are unaffected, once trouble persists past the threshold.
 *
 * It floats over content (no layout shift) and disappears the instant connectivity
 * is restored.
 */
/** Severity order for an episode's peak status (higher = worse). */
const SEVERITY: Record<Exclude<BackendStatus, "online">, number> = {
  waking: 1,
  reconnecting: 2,
  unavailable: 3,
}

type Episode = {
  startedAt: number
  peak: Exclude<BackendStatus, "online">
  dismissed: boolean
}

export function BackendStatusBanner() {
  const status = useBackendStatus()
  const t = useTranslations("backendStatus")
  const [dismissed, setDismissed] = useState(false)
  // The current degradation episode (first non-online status → back online).
  // A ref so re-renders never double-start or double-report.
  const episodeRef = useRef<Episode | null>(null)

  // Expose the dev/preview trigger (window.__nkBackendStatus) for manual UX review,
  // and pre-warm a possibly scaled-to-zero backend as early as possible (this
  // banner is mounted app-wide, so every page that talks to the backend gets it).
  useEffect(() => {
    installBackendPrewarm(__backendPrewarm)
    __installBackendStatusDevHook()
  }, [])

  // Re-arm the dismiss so a *later* outage episode shows again.
  useEffect(() => {
    if (status === "online") setDismissed(false)
  }, [status])

  // Episode tracking. Declared BEFORE the per-status reporting effect so the
  // first banner event of an episode already carries its episode_id.
  useEffect(() => {
    const episode = episodeRef.current
    if (status !== "online") {
      if (!episode) {
        startDegradationEpisode()
        episodeRef.current = { startedAt: Date.now(), peak: status, dismissed: false }
      } else if (SEVERITY[status] > SEVERITY[episode.peak]) {
        episode.peak = status
      }
      return
    }
    if (!episode) return
    // Back online: exactly one recovery event per episode, then close it.
    episodeRef.current = null
    reportDegradationEvent("banner_recovered", {
      duration_ms: Date.now() - episode.startedAt,
      peak_status: episode.peak,
      dismissed: episode.dismissed,
    })
    endDegradationEpisode()
  }, [status])

  useEffect(() => {
    if (dismissed && episodeRef.current) episodeRef.current.dismissed = true
  }, [dismissed])

  // Don't leak an open episode id into later reports if the banner unmounts.
  useEffect(
    () => () => {
      if (episodeRef.current) {
        episodeRef.current = null
        endDegradationEpisode()
      }
    },
    [],
  )

  // Persist every banner display server-side (Andrew's telemetry ask): this is
  // the single global banner instance, so reporting here == "shown to a user".
  // The reporter throttles per type, so a flapping status can't spam.
  useEffect(() => {
    if (status === "online") return
    // A dismissed unavailable card renders nothing — reporting it would count a
    // banner the user never saw. (dismiss only re-arms after passing online.)
    if (status === "unavailable" && dismissed) return
    const debug = getBackendStatusDebug()
    reportDegradationEvent(
      status === "unavailable"
        ? "banner_unavailable"
        : status === "waking"
          ? "banner_waking"
          : "banner_reconnecting",
      {
        stall_ms: debug.oldestStallMs,
        in_flight: debug.inFlightCount,
        probe_ok: debug.lastProbeOk,
        probe_failures: debug.consecutiveProbeFailures,
        since_reachable_ms: debug.sinceReachableMs,
      },
    )
  }, [status, dismissed])

  if (status === "online") return null

  if (status === "waking") {
    return (
      <div
        className="fixed top-3 left-1/2 z-[60] w-[calc(100%-1.5rem)] max-w-md -translate-x-1/2 pointer-events-none"
        role="status"
        aria-live="polite"
        data-testid="backend-waking"
      >
        <div className="flex items-center gap-3 rounded-xl border border-[var(--card-border)] bg-[var(--card)]/95 px-4 py-3 text-[var(--text)] shadow-2xl backdrop-blur">
          <Loader2
            className="h-5 w-5 shrink-0 animate-spin"
            style={{ color: "var(--tenant-primary)" }}
          />
          <div className="space-y-0.5">
            <p className="text-sm font-semibold">{t("waking.title")}</p>
            <p className="text-xs leading-relaxed text-[var(--text-muted)]">{t("waking.body")}</p>
          </div>
        </div>
      </div>
    )
  }

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
