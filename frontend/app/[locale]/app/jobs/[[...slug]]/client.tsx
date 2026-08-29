"use client"

import { useEffect, useState, useCallback } from "react"
import { useAuth } from "@/lib/auth"
import { api, Job, createLyricsReviewApiClient, lyricsReviewApi } from "@/lib/api"
import { Spinner } from "@/components/ui/spinner"
import { Button } from "@/components/ui/button"
import { ArrowLeft, AlertCircle } from "lucide-react"
import { Link } from "@/i18n/routing"
import { LyricsAnalyzer } from "@/components/lyrics-review"
import { InstrumentalSelector } from "@/components/instrumental-review"
import { AudioEditor } from "@/components/audio-editor/AudioEditor"
import { ThemeToggle } from "@/components/ThemeToggle"
import CrashReportBoundary from "@/components/CrashReportBoundary"
import type { CorrectionData } from "@/lib/lyrics-review/types"
import { isLocalMode, createLocalModeJob } from "@/lib/local-mode"
import {
  parseRouteFromPathname,
  parseRouteFromHash,
  type RouteType,
} from "@/lib/job-router-routes"

type AccessState =
  | { status: "loading" }
  | { status: "not_authenticated" }
  | { status: "not_authorized"; reason: string }
  | { status: "job_not_found" }
  | { status: "wrong_state"; currentState: string; expectedStates: string[] }
  | { status: "invalid_route" }
  | { status: "authorized"; job: Job; routeType: RouteType }
  | { status: "local_mode"; job: Job; routeType: RouteType }

function getExpectedStates(routeType: RouteType): string[] {
  switch (routeType) {
    case "review":
      return ["awaiting_review", "in_review"]
    case "instrumental":
      return ["awaiting_review", "in_review"] // Same states for now
    case "audio-edit":
      return ["awaiting_audio_edit", "in_audio_edit"]
    default:
      return []
  }
}

export function JobRouterClient() {
  // For static exports, useParams() returns empty object
  // We need to parse the URL path directly instead
  const [pathname, setPathname] = useState(() =>
    typeof window !== 'undefined' ? window.location.pathname : ''
  )

  // Track hash for re-renders when hash changes (cloud mode)
  const [hash, setHash] = useState(() =>
    typeof window !== 'undefined' ? window.location.hash : ''
  )

  // Replay mode: read-only re-open of a COMPLETED job's review UI for the
  // fully-automated-review recording program. Enabled via ?replay=1 (admin only).
  const [isReplay] = useState(() =>
    typeof window !== 'undefined' &&
    new URLSearchParams(window.location.search).get('replay') === '1'
  )

  // Listen for hash changes
  useEffect(() => {
    const handleHashChange = () => {
      setHash(window.location.hash)
    }
    window.addEventListener('hashchange', handleHashChange)
    return () => window.removeEventListener('hashchange', handleHashChange)
  }, [])

  // Update pathname on mount (needed for SSR hydration)
  useEffect(() => {
    setPathname(window.location.pathname)
  }, [])

  // Determine routing mode and parse route
  // - Local mode: Use path-based routing parsed from window.location.pathname
  // - Cloud mode: Use hash-based routing (e.g., /app/jobs/#/{jobId}/review)
  const inLocalMode = isLocalMode()
  const { jobId, routeType } = inLocalMode
    ? parseRouteFromPathname(pathname)
    : parseRouteFromHash(hash)

  const { user, isLoading: authLoading, hasHydrated } = useAuth()
  const [accessState, setAccessState] = useState<AccessState>({ status: "loading" })

  useEffect(() => {
    async function checkAccess() {
      // Handle local mode (skip all auth checks)
      if (inLocalMode) {
        // In local mode, always use review (combined lyrics + instrumental)
        const localRouteType: RouteType = routeType === "unknown" ? "review" : routeType

        // Create mock job for local mode
        const localJob = createLocalModeJob({ routeType: localRouteType }) as Job
        setAccessState({ status: "local_mode", job: localJob, routeType: localRouteType })
        return
      }

      // Cloud mode: Check hash-based route
      if (!jobId || routeType === "unknown") {
        setAccessState({ status: "invalid_route" })
        return
      }

      // Wait for auth to finish loading and hydration to complete
      // This prevents a flash of "Sign in required" before auth state is restored from localStorage
      if (authLoading || !hasHydrated) return

      // Must be authenticated
      if (!user) {
        setAccessState({ status: "not_authenticated" })
        return
      }

      try {
        // Fetch job details
        const job = await api.getJob(jobId)

        // Check ownership: user must own the job or be admin
        const isOwner = job.user_email === user.email
        const isAdmin = user.role === "admin"

        if (!isOwner && !isAdmin) {
          setAccessState({
            status: "not_authorized",
            reason: "You don't have permission to access this job"
          })
          return
        }

        // Check job is in correct state.
        // Replay mode (admin only) bypasses the state gate so a COMPLETED job's
        // review UI can be re-opened read-only.
        const expectedStates = getExpectedStates(routeType)
        if (!(isReplay && isAdmin) && !expectedStates.includes(job.status)) {
          setAccessState({
            status: "wrong_state",
            currentState: job.status,
            expectedStates
          })
          return
        }

        // All checks passed
        setAccessState({ status: "authorized", job, routeType })
      } catch (error: unknown) {
        // Job not found or API error
        if (error && typeof error === "object" && "status" in error && error.status === 404) {
          setAccessState({ status: "job_not_found" })
        } else {
          setAccessState({
            status: "not_authorized",
            reason: "Failed to load job details"
          })
        }
      }
    }

    checkAccess()
  }, [inLocalMode, jobId, routeType, user, authLoading, hasHydrated, isReplay])

  // Loading state
  if (accessState.status === "loading") {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center">
          <Spinner className="w-8 h-8 mx-auto mb-4" />
          <p className="text-muted-foreground">Loading...</p>
        </div>
      </div>
    )
  }

  // Invalid route
  if (accessState.status === "invalid_route") {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center max-w-md p-6">
          <AlertCircle className="w-12 h-12 mx-auto mb-4 text-red-500" />
          <h1 className="text-xl font-semibold mb-2">Page not found</h1>
          <p className="text-muted-foreground mb-4">
            The page you&apos;re looking for doesn&apos;t exist.
          </p>
          <Button variant="outline" asChild>
            <Link href="/app">
              <ArrowLeft className="w-4 h-4 mr-2" />
              Back to dashboard
            </Link>
          </Button>
        </div>
      </div>
    )
  }

  // Not authenticated
  if (accessState.status === "not_authenticated") {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center max-w-md p-6">
          <AlertCircle className="w-12 h-12 mx-auto mb-4 text-amber-500" />
          <h1 className="text-xl font-semibold mb-2">Sign in required</h1>
          <p className="text-muted-foreground mb-4">
            You need to sign in to access this page.
          </p>
          <Button asChild>
            <Link href="/app">Sign in</Link>
          </Button>
        </div>
      </div>
    )
  }

  // Not authorized
  if (accessState.status === "not_authorized") {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center max-w-md p-6">
          <AlertCircle className="w-12 h-12 mx-auto mb-4 text-red-500" />
          <h1 className="text-xl font-semibold mb-2">Access denied</h1>
          <p className="text-muted-foreground mb-4">{accessState.reason}</p>
          <Button variant="outline" asChild>
            <Link href="/app">
              <ArrowLeft className="w-4 h-4 mr-2" />
              Back to dashboard
            </Link>
          </Button>
        </div>
      </div>
    )
  }

  // Job not found
  if (accessState.status === "job_not_found") {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center max-w-md p-6">
          <AlertCircle className="w-12 h-12 mx-auto mb-4 text-red-500" />
          <h1 className="text-xl font-semibold mb-2">Job not found</h1>
          <p className="text-muted-foreground mb-4">
            The job you&apos;re looking for doesn&apos;t exist or has been deleted.
          </p>
          <Button variant="outline" asChild>
            <Link href="/app">
              <ArrowLeft className="w-4 h-4 mr-2" />
              Back to dashboard
            </Link>
          </Button>
        </div>
      </div>
    )
  }

  // Wrong state
  if (accessState.status === "wrong_state") {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center max-w-md p-6">
          <AlertCircle className="w-12 h-12 mx-auto mb-4 text-amber-500" />
          <h1 className="text-xl font-semibold mb-2">Not available</h1>
          <p className="text-muted-foreground mb-4">
            This job is currently in &quot;{accessState.currentState}&quot; state and is not ready for review.
          </p>
          <Button variant="outline" asChild>
            <Link href="/app">
              <ArrowLeft className="w-4 h-4 mr-2" />
              Back to dashboard
            </Link>
          </Button>
        </div>
      </div>
    )
  }

  // Authorized or local mode - render the appropriate UI
  const { job } = accessState

  // Get route type from access state (which validated and stored it during checkAccess)
  const currentRouteType = accessState.status === "authorized" || accessState.status === "local_mode"
    ? accessState.routeType
    : "review" // Fallback (shouldn't happen since we return early for other statuses)

  if (currentRouteType === "audio-edit") {
    return <AudioEditor job={job} />
  }

  if (currentRouteType === "instrumental") {
    return (
      <CrashReportBoundary source="instrumental-review" backHref="/app">
        <InstrumentalReviewWrapper job={job} isLocalMode={inLocalMode} isReplay={isReplay} />
      </CrashReportBoundary>
    )
  }

  return (
    <CrashReportBoundary source="lyrics-review" backHref="/app">
      <LyricsReviewWrapper job={job} isLocalMode={inLocalMode} isReplay={isReplay} />
    </CrashReportBoundary>
  )
}

// Lyrics Review Component Wrapper
function LyricsReviewWrapper({ job, isLocalMode = false, isReplay = false }: { job: Job; isLocalMode?: boolean; isReplay?: boolean }) {
  const [correctionData, setCorrectionData] = useState<CorrectionData | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  // Replay: toggle between the final lyrics and the "post-AI, pre-human" state so
  // manual edits are obvious. Reset whenever the job changes.
  const [showPostAi, setShowPostAi] = useState(false)
  useEffect(() => { setShowPostAi(false) }, [job.job_id])

  // DEV-ONLY: `&edit=1` makes a replay session editable so the review UI (incl. Tap To Sync)
  // can be exercised against REAL job data + audio locally. Ignored in production builds — replay
  // stays strictly read-only there.
  const replayEditable =
    isReplay &&
    process.env.NODE_ENV !== 'production' &&
    typeof window !== 'undefined' &&
    new URLSearchParams(window.location.search).get('edit') === '1'

  // Create the API client for this job
  const apiClient = createLyricsReviewApiClient(job.job_id)

  // Load correction data
  useEffect(() => {
    async function loadData() {
      try {
        setIsLoading(true)
        setError(null)
        const data = await lyricsReviewApi.getCorrectionData(job.job_id, { replay: isReplay })
        setCorrectionData(data)
      } catch (err) {
        console.error("Failed to load correction data:", err)
        setError(err instanceof Error ? err.message : "Failed to load lyrics data")
      } finally {
        setIsLoading(false)
      }
    }
    loadData()
  }, [job.job_id, isReplay])

  // File load handler (opens file picker for local file)
  const handleFileLoad = useCallback(() => {
    // For now, this is a no-op since we load from API
    // Could be extended to allow loading from local files
    console.log("File load requested")
  }, [])

  // Metadata handler
  const handleShowMetadata = useCallback(() => {
    // Could show a modal with job metadata
    console.log("Show metadata requested", job)
  }, [job])

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center">
          <Spinner className="w-8 h-8 mx-auto mb-4" />
          <p className="text-muted-foreground">Loading lyrics data...</p>
        </div>
      </div>
    )
  }

  if (error || !correctionData) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center max-w-md p-6">
          <AlertCircle className="w-12 h-12 mx-auto mb-4 text-red-500" />
          <h1 className="text-xl font-semibold mb-2">Failed to load lyrics</h1>
          <p className="text-muted-foreground mb-4">
            {error || "Could not load lyrics data for this job."}
          </p>
          {!isLocalMode && (
            <Button variant="outline" asChild>
              <Link href="/app">
                <ArrowLeft className="w-4 h-4 mr-2" />
                Back to dashboard
              </Link>
            </Button>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-background">
      {/* App Header - matches old frontend's AppHeader */}
      <header className="border-b bg-card/80 backdrop-blur-sm sticky top-0 z-50 px-4 py-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            {!isLocalMode && (
              <Button variant="ghost" size="sm" asChild>
                <Link href="/app">
                  <ArrowLeft className="w-4 h-4 mr-2" />
                  Back
                </Link>
              </Button>
            )}
{/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src="/nomad-karaoke-logo.svg"
              alt="Nomad Karaoke"
              style={{ height: 40 }}
            />
            <h1 className="text-lg font-bold">
              {isReplay ? (replayEditable ? "Lyrics Review — Replay (editable · dev)" : "Lyrics Review — Replay (read-only)") : "Lyrics Transcription Review"}
            </h1>
            {(job.artist || job.title) && (
              <span className="text-xs md:text-sm text-muted-foreground truncate">
                {[job.artist, job.title].filter(Boolean).join(" - ")}
              </span>
            )}
          </div>
          <ThemeToggle />
        </div>
      </header>

      {isReplay && <ReplayNavBar jobId={job.job_id} screen="review" />}
      {isReplay && (
        <ReplayActionLog
          editLog={correctionData.replay?.edit_log ?? null}
          instrumentalSelection={correctionData.replay?.instrumental_selection ?? null}
          jobStatus={correctionData.replay?.job_status ?? job.status}
        />
      )}

      {isReplay && correctionData.replay?.has_manual_edits && correctionData.replay?.post_ai_segments && (
        <div className="mx-4 mt-2 flex items-center gap-2 rounded-lg border border-amber-400/40 bg-amber-50/40 dark:bg-amber-900/10 px-3 py-2 text-sm">
          <span className="text-muted-foreground">Viewing lyrics:</span>
          <button
            className={`px-3 py-1 rounded font-medium ${!showPostAi ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground hover:bg-muted/70"}`}
            onClick={() => setShowPostAi(false)}
          >Final (my result)</button>
          <button
            className={`px-3 py-1 rounded font-medium ${showPostAi ? "bg-amber-500 text-white" : "bg-muted text-muted-foreground hover:bg-muted/70"}`}
            onClick={() => setShowPostAi(true)}
          >Post-AI (pre-human)</button>
          <span className="text-xs text-muted-foreground ml-1">
            {showPostAi ? "← what the AI produced, before your manual edits" : "← your final version"}
          </span>
        </div>
      )}

      <main className="px-4 py-2">
        <LyricsAnalyzer
          key={showPostAi ? "postai" : "final"}
          data={
            showPostAi && correctionData.replay?.post_ai_segments
              ? { ...correctionData, corrected_segments: correctionData.replay.post_ai_segments }
              : correctionData
          }
          onFileLoad={handleFileLoad}
          onShowMetadata={handleShowMetadata}
          apiClient={apiClient}
          isReadOnly={isReplay && !replayEditable}
          audioHash={correctionData.metadata?.audio_hash || job.audio_hash || job.job_id}
          isLocalMode={isLocalMode}
          jobId={job.job_id}
          hasExistingInstrumental={!!job.existing_instrumental_gcs_path}
        />
      </main>
    </div>
  )
}

/**
 * ReplayNavBar — replay-only navigation: toggle Lyrics⇄Instrumental for the current
 * job, and step Prev/Next through the job list (passed as a comma-separated `queue`
 * URL param). All navigation is hash-only (`#/<id>/<screen>`), so the query params
 * (baseApiUrl, replay, queue) are preserved and the app re-renders via hashchange.
 */
function ReplayNavBar({ jobId, screen }: { jobId: string; screen: "review" | "instrumental" }) {
  const queue = (typeof window !== "undefined"
    ? new URLSearchParams(window.location.search).get("queue")
    : null)?.split(",").map((s) => s.trim()).filter(Boolean) ?? []
  const idx = queue.indexOf(jobId)
  const prev = idx > 0 ? queue[idx - 1] : null
  const next = idx >= 0 && idx < queue.length - 1 ? queue[idx + 1] : null
  const go = (id: string, s: "review" | "instrumental") => { window.location.hash = `#/${id}/${s}` }

  const tabCls = (active: boolean) =>
    `px-3 py-1 rounded text-sm font-medium ${active ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground hover:bg-muted/70"}`
  const navCls = (enabled: boolean) =>
    `px-3 py-1 rounded text-sm font-medium border ${enabled ? "hover:bg-muted" : "opacity-40 cursor-not-allowed"}`

  return (
    <div className="mx-4 mt-2 flex items-center gap-2 rounded-lg border bg-card px-3 py-2">
      <span className="text-xs text-muted-foreground mr-1">Replay:</span>
      <button className={tabCls(screen === "review")} onClick={() => go(jobId, "review")}>Lyrics</button>
      <button className={tabCls(screen === "instrumental")} onClick={() => go(jobId, "instrumental")}>Instrumental</button>
      <div className="ml-auto flex items-center gap-2">
        <button className={navCls(!!prev)} disabled={!prev} onClick={() => prev && go(prev, "review")}>← Prev</button>
        {idx >= 0 && queue.length > 0 && (
          <span className="text-xs text-muted-foreground tabular-nums">{idx + 1} / {queue.length}</span>
        )}
        <button className={navCls(!!next)} disabled={!next} onClick={() => next && go(next, "review")}>Next job →</button>
      </div>
    </div>
  )
}

/**
 * ReplayActionLog — read-only summary of the reviewer's ordered edit_log for a
 * completed job, shown in replay mode so Andrew can narrate what he did and why.
 * Distinguishes AI-accepted / AI-rejected / manual / timing ops. Internal admin
 * tool (English-only, like /admin).
 */
function ReplayActionLog({
  editLog,
  instrumentalSelection,
  jobStatus,
}: {
  editLog: import("@/lib/lyrics-review/types").EditLog | null
  instrumentalSelection: string | null
  jobStatus: string
}) {
  const [collapsed, setCollapsed] = useState(false)
  const entries = editLog?.entries ?? []

  const counts = entries.reduce(
    (acc, e) => {
      if (e.operation === "ai_suggestion_accept") acc.aiAccept++
      else if (e.operation === "ai_suggestion_reject") acc.aiReject++
      else if (e.operation === "timing_change") acc.timing++
      else if (e.operation === "ai_suggestion_run" || e.operation === "ai_suggestion_undo") acc.other++
      else acc.manual++
      return acc
    },
    { aiAccept: 0, aiReject: 0, manual: 0, timing: 0, other: 0 }
  )

  const badge = (op: string): { label: string; cls: string } => {
    if (op === "ai_suggestion_accept") return { label: "AI ✓", cls: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300" }
    if (op === "ai_suggestion_reject") return { label: "AI ✗", cls: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300" }
    if (op === "timing_change") return { label: "timing", cls: "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300" }
    if (op.startsWith("ai_suggestion")) return { label: op.replace("ai_suggestion_", "AI "), cls: "bg-muted text-muted-foreground" }
    return { label: "manual", cls: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300" }
  }

  return (
    <div className="mx-4 my-2 rounded-lg border bg-card">
      <button
        onClick={() => setCollapsed((c) => !c)}
        className="w-full flex items-center justify-between px-4 py-2 text-sm font-medium"
      >
        <span>
          Replay — actions in this review:{" "}
          <span className="text-green-700 dark:text-green-400">{counts.aiAccept} AI accepted</span>,{" "}
          <span className="text-red-700 dark:text-red-400">{counts.aiReject} AI rejected</span>,{" "}
          <span className="text-amber-700 dark:text-amber-400">{counts.manual} manual</span>,{" "}
          <span className="text-blue-700 dark:text-blue-400">{counts.timing} timing</span>
          {"  ·  instrumental: "}
          <code>{instrumentalSelection ?? "—"}</code>
          {"  ·  status: "}
          <code>{jobStatus}</code>
        </span>
        <span className="text-muted-foreground">{collapsed ? "▸ show" : "▾ hide"}</span>
      </button>
      {!collapsed && (
        <div className="max-h-64 overflow-y-auto border-t px-2 py-2 text-xs">
          {entries.length === 0 && (
            <p className="px-2 py-1 text-muted-foreground">
              No edit log recorded for this job.
            </p>
          )}
          <ol className="space-y-0.5">
            {entries.map((e, i) => {
              const b = badge(e.operation)
              const cat = (e.details?.category as string) || (e.details?.gap_category as string) || ""
              return (
                <li key={e.id || i} className="flex items-start gap-2 px-2 py-1 rounded hover:bg-muted/50">
                  <span className="text-muted-foreground tabular-nums w-6 text-right">{i + 1}</span>
                  <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${b.cls}`}>{b.label}</span>
                  <span className="flex-1 break-words">
                    {e.text_before && <span className="line-through text-muted-foreground">{e.text_before}</span>}
                    {e.text_before && e.text_after && <span className="mx-1">→</span>}
                    {e.text_after && <span className="font-medium">{e.text_after}</span>}
                    {!e.text_before && !e.text_after && <span className="text-muted-foreground">{e.operation}</span>}
                    {cat && <span className="ml-2 text-muted-foreground">[{cat}]</span>}
                  </span>
                </li>
              )
            })}
          </ol>
        </div>
      )}
    </div>
  )
}

// Instrumental Review Component Wrapper
// The InstrumentalSelector component handles its own data fetching and submission
// It uses the appropriate API based on isLocalMode
function InstrumentalReviewWrapper({ job, isLocalMode = false, isReplay = false }: { job: Job; isLocalMode?: boolean; isReplay?: boolean }) {
  return (
    <>
      {isReplay && <ReplayNavBar jobId={job.job_id} screen="instrumental" />}
      <InstrumentalSelector job={job} isLocalMode={isLocalMode} isReadOnly={isReplay} />
    </>
  )
}

