"use client"

import { useEffect, useState, useCallback } from "react"
import { useParams, useRouter } from "next/navigation"
import { useAuth } from "@/lib/auth"
import { api, Job, createLyricsReviewApiClient, lyricsReviewApi } from "@/lib/api"
import { Spinner } from "@/components/ui/spinner"
import { Button } from "@/components/ui/button"
import { ArrowLeft, AlertCircle } from "lucide-react"
import Link from "next/link"
import { InstrumentalSelector } from "@/components/instrumental-review"
import { LyricsAnalyzer } from "@/components/lyrics-review"
import { ThemeToggle } from "@/components/ThemeToggle"
import type { CorrectionData } from "@/lib/lyrics-review/types"
import { isLocalMode, createLocalModeJob, getLocalJobId } from "@/lib/local-mode"

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

function parseRoute(slug: string[] | undefined): { jobId: string | null; routeType: RouteType } {
  if (!slug || slug.length === 0) {
    return { jobId: null, routeType: "unknown" }
  }

  // Expected formats:
  // [jobId, "review"] -> /app/jobs/{jobId}/review
  // [jobId, "instrumental"] -> /app/jobs/{jobId}/instrumental
  if (slug.length === 2) {
    const [jobId, action] = slug
    if (action === "review") {
      return { jobId, routeType: "review" }
    }
    if (action === "instrumental") {
      return { jobId, routeType: "instrumental" }
    }
  }

  return { jobId: null, routeType: "unknown" }
}

function getExpectedStates(routeType: RouteType): string[] {
  switch (routeType) {
    case "review":
      return ["awaiting_review", "in_review"]
    case "instrumental":
      return ["awaiting_instrumental_selection"]
    default:
      return []
  }
}

// Check for pending SPA redirect and return the stored path if found
// This is used on GitHub Pages where dynamic routes aren't pre-rendered
function getStoredRedirectPath(): string | null {
  if (typeof window === 'undefined') return null

  // Only check for redirect restoration if we're at the redirect target (/app/jobs/)
  // This prevents a race condition where React on 404.html clears sessionStorage
  // before the window.location.replace() redirect completes
  const pathname = window.location.pathname
  if (pathname !== '/app/jobs/' && pathname !== '/app/jobs') {
    return null
  }

  const redirectPath = sessionStorage.getItem('spa-redirect-path')
  if (redirectPath) {
    // DON'T remove sessionStorage here - we need it to survive page reload
    // Next.js will revert any history.replaceState changes, so the URL stays at /app/jobs/
    // On reload, the browser requests /app/jobs/, 404.html redirects back here, and we re-read the path
    // The sessionStorage is cleared when user navigates to a different job or page
    return redirectPath
  }
  return null
}

// Clear the stored redirect path - called when we're done with it
function clearStoredRedirectPath(): void {
  if (typeof window !== 'undefined') {
    sessionStorage.removeItem('spa-redirect-path')
  }
}

// Parse route from a stored redirect path
function parseRouteFromPath(path: string): { jobId: string | null; routeType: RouteType } {
  // Remove query string and hash for matching
  const cleanPath = path.split('?')[0].split('#')[0]
  const match = cleanPath.match(/^\/app\/jobs\/([^/]+)\/(review|instrumental)\/?$/)

  if (match) {
    const [, jobId, action] = match
    return { jobId, routeType: action as RouteType }
  }
  return { jobId: null, routeType: "unknown" }
}

export function JobRouterClient() {
  const params = useParams()
  const router = useRouter()
  const slug = params.slug as string[] | undefined

  // Check for stored redirect path from GitHub Pages SPA redirect
  // Using lazy initializer to run only once and avoid SSR issues
  const [storedRedirectPath] = useState(() => getStoredRedirectPath())

  // Parse route: use stored redirect path if available, otherwise use Next.js params
  // Note: sessionStorage persists across reloads to support page refresh
  const { jobId, routeType } = storedRedirectPath
    ? parseRouteFromPath(storedRedirectPath)
    : parseRoute(slug)

  // Clear stored redirect path when navigating to a different job
  // This prevents stale redirects when clicking "Review Lyrics" on a different job
  useEffect(() => {
    if (storedRedirectPath && jobId) {
      const storedJobId = parseRouteFromPath(storedRedirectPath).jobId
      // If the stored job ID doesn't match the current job ID, clear it
      // This handles the case where user navigates to a different job
      if (storedJobId && storedJobId !== jobId) {
        clearStoredRedirectPath()
      }
    }
  }, [storedRedirectPath, jobId])

  const { user, isLoading: authLoading, hasHydrated } = useAuth()
  const [accessState, setAccessState] = useState<AccessState>({ status: "loading" })

  useEffect(() => {
    async function checkAccess() {
      // Check for local mode first (skip all auth checks)
      if (isLocalMode()) {
        // In local mode, determine route type from URL or default to review
        // The local server will redirect to /app/jobs/local/review or /app/jobs/local/instrumental
        let localRouteType: RouteType = routeType

        // If route is unknown but we're in local mode, try to determine from path
        if (localRouteType === "unknown") {
          const path = typeof window !== 'undefined' ? window.location.pathname : ''
          if (path.includes('instrumental')) {
            localRouteType = "instrumental"
          } else {
            localRouteType = "review" // Default to review in local mode
          }
        }

        // Create mock job for local mode
        const localJob = createLocalModeJob({ routeType: localRouteType }) as Job
        setAccessState({ status: "local_mode", job: localJob, routeType: localRouteType })
        return
      }

      // Invalid route (cloud mode)
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
  }, [jobId, routeType, user, authLoading, hasHydrated])

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
    const actionName = routeType === "review" ? "lyrics review" : "instrumental selection"
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center max-w-md p-6">
          <AlertCircle className="w-12 h-12 mx-auto mb-4 text-amber-500" />
          <h1 className="text-xl font-semibold mb-2">Not available</h1>
          <p className="text-muted-foreground mb-4">
            This job is currently in &quot;{accessState.currentState}&quot; state and is not ready for {actionName}.
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
  const { job, routeType: authorizedRouteType } = accessState
  const inLocalMode = accessState.status === "local_mode"

  if (authorizedRouteType === "review") {
    return <LyricsReviewWrapper job={job} isLocalMode={inLocalMode} />
  }

  if (authorizedRouteType === "instrumental") {
    return <InstrumentalSelector job={job} isLocalMode={inLocalMode} />
  }

  return null
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
        />
      </main>
    </div>
  )
}

