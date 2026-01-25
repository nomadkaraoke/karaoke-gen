"use client"

import { useEffect, useState, useCallback } from "react"
import { useAuth } from "@/lib/auth"
import { api, Job, createLyricsReviewApiClient, lyricsReviewApi } from "@/lib/api"
import { Spinner } from "@/components/ui/spinner"
import { Button } from "@/components/ui/button"
import { ArrowLeft, AlertCircle } from "lucide-react"
import Link from "next/link"
import { LyricsAnalyzer } from "@/components/lyrics-review"
import { InstrumentalSelector } from "@/components/instrumental-review"
import { ThemeToggle } from "@/components/ThemeToggle"
import type { CorrectionData } from "@/lib/lyrics-review/types"
import { isLocalMode, createLocalModeJob } from "@/lib/local-mode"

type RouteType = "review" | "instrumental" | "unknown"

type AccessState =
  | { status: "loading" }
  | { status: "not_authenticated" }
  | { status: "not_authorized"; reason: string }
  | { status: "job_not_found" }
  | { status: "wrong_state"; currentState: string; expectedStates: string[] }
  | { status: "invalid_route" }
  | { status: "authorized"; job: Job; routeType: RouteType }
  | { status: "local_mode"; job: Job; routeType: RouteType }

// Parse route from window.location.pathname (used for local mode with path-based routing)
// For static exports, useParams() returns empty - we must parse the URL directly
function parseRouteFromPathname(pathname: string): { jobId: string | null; routeType: RouteType } {
  if (!pathname) {
    return { jobId: null, routeType: "unknown" }
  }

  // Expected format: /app/jobs/{jobId}/{action}
  // e.g., /app/jobs/local/review or /app/jobs/local/instrumental
  const match = pathname.match(/^\/app\/jobs\/([^/]+)\/(review|instrumental)\/?$/)

  if (match) {
    const [, jobId, action] = match
    return { jobId, routeType: action as RouteType }
  }

  return { jobId: null, routeType: "unknown" }
}

// Parse route from URL hash (used for cloud mode)
// Expected format: #/{jobId}/review
function parseRouteFromHash(hash: string): { jobId: string | null; routeType: RouteType } {
  if (!hash || hash.length <= 1) {
    return { jobId: null, routeType: "unknown" }
  }

  // Remove the leading '#' and parse
  const hashPath = hash.substring(1)
  const match = hashPath.match(/^\/?([^/]+)\/review\/?$/)

  if (match) {
    const [, jobId] = match
    return { jobId, routeType: "review" }
  }
  return { jobId: null, routeType: "unknown" }
}

function getExpectedStates(routeType: RouteType): string[] {
  switch (routeType) {
    case "review":
      return ["awaiting_review", "in_review"]
    case "instrumental":
      return ["awaiting_review", "in_review"] // Same states for now
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

        // Check job is in correct state
        const expectedStates = getExpectedStates(routeType)
        if (!expectedStates.includes(job.status)) {
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
  }, [inLocalMode, jobId, routeType, user, authLoading, hasHydrated])

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

  if (currentRouteType === "instrumental") {
    return <InstrumentalReviewWrapper job={job} isLocalMode={inLocalMode} />
  }

  return <LyricsReviewWrapper job={job} isLocalMode={inLocalMode} />
}

// Lyrics Review Component Wrapper
function LyricsReviewWrapper({ job, isLocalMode = false }: { job: Job; isLocalMode?: boolean }) {
  const [correctionData, setCorrectionData] = useState<CorrectionData | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Create the API client for this job
  const apiClient = createLyricsReviewApiClient(job.job_id)

  // Load correction data
  useEffect(() => {
    async function loadData() {
      try {
        setIsLoading(true)
        setError(null)
        const data = await lyricsReviewApi.getCorrectionData(job.job_id)
        setCorrectionData(data)
      } catch (err) {
        console.error("Failed to load correction data:", err)
        setError(err instanceof Error ? err.message : "Failed to load lyrics data")
      } finally {
        setIsLoading(false)
      }
    }
    loadData()
  }, [job.job_id])

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
            <h1 className="text-lg font-bold">Lyrics Transcription Review</h1>
          </div>
          <ThemeToggle />
        </div>
      </header>

      <main className="px-4 py-2">
        <LyricsAnalyzer
          data={correctionData}
          onFileLoad={handleFileLoad}
          onShowMetadata={handleShowMetadata}
          apiClient={apiClient}
          isReadOnly={false}
          audioHash={correctionData.metadata?.audio_hash || job.audio_hash || job.job_id}
          isLocalMode={isLocalMode}
        />
      </main>
    </div>
  )
}

// Instrumental Review Component Wrapper
function InstrumentalReviewWrapper({ job, isLocalMode = false }: { job: Job; isLocalMode?: boolean }) {
  return (
    <div className="min-h-screen bg-background">
      {/* App Header */}
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
            <h1 className="text-lg font-bold">Instrumental Selection</h1>
          </div>
          <ThemeToggle />
        </div>
      </header>

      <main className="px-4 py-2">
        <InstrumentalSelector job={job} isLocalMode={isLocalMode} />
      </main>
    </div>
  )
}

