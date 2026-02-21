"use client"

import { useState, useEffect, useCallback, useMemo, Suspense, useRef } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { api, Job, getAccessToken } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { useAdminSettings } from "@/lib/admin-settings"
import { useJobNotifications, useVisibilityRefresh } from "@/hooks/use-notifications"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Music2, RefreshCw, Loader2, Moon, Sun, Eye, EyeOff } from "lucide-react"
import { sortJobsByPriority, getDisplayJobs } from "@/lib/job-status"
import { WarmingUpLoader } from "@/components/WarmingUpLoader"
import { JobCard } from "@/components/job"
import { JobSubmission } from "@/components/job/JobSubmission"
import { AuthStatus } from "@/components/auth"
import { AutoProcessor } from "@/components/AutoProcessor"
import { VersionFooter } from "@/components/version-footer"
import { PushNotificationPrompt } from "@/components/push-notification-prompt"
import { useTheme } from "@/lib/theme"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"

function AppPageContent() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const [allJobs, setAllJobs] = useState<Job[]>([])
  const [isLoadingJobs, setIsLoadingJobs] = useState(true)
  const [isInitialLoad, setIsInitialLoad] = useState(true)
  const [isAuthenticated, setIsAuthenticated] = useState<boolean | null>(null)
  const [isVerifyingToken, setIsVerifyingToken] = useState(false)
  const adminTokenHandled = useRef(false) // Track if admin_token was already processed
  const { isDarkMode, toggleTheme, mounted } = useTheme()
  const { user, fetchUser, verifyMagicLink } = useAuth()
  const { showTestData } = useAdminSettings()
  const [jobLimit, setJobLimit] = useState<number>(() => {
    if (typeof window === "undefined") return 5
    const saved = localStorage.getItem("nomad-karaoke-job-limit")
    return saved ? Number(saved) : 5
  })
  const [hideCompleted, setHideCompleted] = useState<boolean>(() => {
    if (typeof window === "undefined") return false
    return localStorage.getItem("nomad-karaoke-hide-completed") === "true"
  })

  // Check if user is admin (for exclude_test parameter)
  const isAdmin = user?.role === "admin" || user?.email?.endsWith("@nomadkaraoke.com")

  // Derive displayed jobs from allJobs + display limit + filter (instant, no re-fetch)
  const { displayedJobs: jobs, totalFetched } = useMemo(
    () => getDisplayJobs(allJobs, jobLimit, hideCompleted),
    [allJobs, jobLimit, hideCompleted]
  )

  // Memoize loadJobs for use with visibility refresh
  const loadJobs = useCallback(async () => {
    if (!getAccessToken()) {
      setIsLoadingJobs(false)
      setIsInitialLoad(false)
      return
    }
    try {
      // Always fetch 100 jobs; display limit is applied client-side
      const data = await api.listJobs({
        limit: 100,
        exclude_test: isAdmin ? !showTestData : undefined
      })
      // Sort: blocking jobs first, then processing, then completed
      setAllJobs(sortJobsByPriority(data))
    } catch (err: any) {
      // Don't log auth errors - user just needs to authenticate
      if (err?.status !== 401) {
        console.error("Failed to load jobs:", err)
      }
    } finally {
      setIsLoadingJobs(false)
      setIsInitialLoad(false)
    }
  }, [isAdmin, showTestData])

  // Enable notifications for job status changes (sound + title animation)
  useJobNotifications(allJobs)

  // Refresh jobs immediately when tab becomes visible (after returning from review UIs)
  useVisibilityRefresh(loadJobs, isAuthenticated === true)

  // Handle admin_token URL parameter for one-click login from email
  useEffect(() => {
    const adminToken = searchParams.get("admin_token")

    // Only process admin_token once (use ref to prevent repeated calls)
    if (adminToken && !adminTokenHandled.current) {
      adminTokenHandled.current = true
      setIsVerifyingToken(true)

      verifyMagicLink(adminToken).then((success) => {
        // Clean URL by removing the token
        window.history.replaceState({}, '', '/app')
        setIsVerifyingToken(false)
        if (success) {
          setIsAuthenticated(true)
        } else {
          router.replace('/')
        }
      })
      return
    }

    // Normal auth check (skip if admin_token is being processed)
    if (adminToken || isVerifyingToken) return

    const token = getAccessToken()
    if (!token) {
      // Redirect to landing page if not authenticated
      router.replace('/')
      return
    }
    setIsAuthenticated(true)
  }, [router, searchParams, verifyMagicLink, isVerifyingToken])

  // Ensure user data is loaded after authentication
  // This handles the case where user navigates via client-side navigation
  // (e.g., after beta enrollment) and the module-level fetchUser() didn't run
  useEffect(() => {
    const token = getAccessToken()
    if (token && !user) {
      fetchUser()
    }
  }, [user, fetchUser])

  // Load jobs on mount (only if authenticated)
  useEffect(() => {
    if (isAuthenticated !== true) {
      setIsLoadingJobs(false)
      return
    }
    loadJobs()
    // Poll for updates every 10 seconds
    const interval = setInterval(loadJobs, 10000)
    return () => clearInterval(interval)
  }, [isAuthenticated, loadJobs])

  // Show nothing while checking auth or verifying admin token (prevents flash)
  if (isAuthenticated === null || isVerifyingToken) {
    return (
      <div className="min-h-screen flex items-center justify-center animated-gradient">
        <Loader2 className="w-8 h-8 animate-spin" style={{ color: 'var(--text-muted)' }} />
      </div>
    )
  }

  // Show warming up loader during initial job load (cold start scenario)
  if (isInitialLoad && isLoadingJobs) {
    return (
      <div className="min-h-screen flex items-center justify-center animated-gradient">
        <WarmingUpLoader spinnerClassName="w-10 h-10" />
      </div>
    )
  }

  return (
    <div className="min-h-screen animated-gradient">
      {/* AutoProcessor - handles non-interactive mode for jobs with that flag */}
      <AutoProcessor jobs={allJobs} onJobsChanged={loadJobs} />

      {/* Header */}
      <header className="fixed top-0 left-0 right-0 z-50 bg-dark-900/80 backdrop-blur-md border-b border-dark-700">
        <div className="px-3 sm:px-4 py-2 sm:py-3 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 sm:gap-3 min-w-0">
            <img src="/nomad-karaoke-logo.svg" alt="Nomad Karaoke" className="h-8 sm:h-10 shrink-0" />
            <div className="min-w-0">
              <h1 className="text-base sm:text-xl font-bold truncate" style={{ color: 'var(--text)' }}>Karaoke Generator</h1>
            </div>
          </div>
          <div className="flex items-center gap-1 sm:gap-2 shrink-0">
            <Button
              variant="ghost"
              size="sm"
              onClick={loadJobs}
              disabled={isLoadingJobs || !isAuthenticated}
              className="min-h-[40px] px-2 sm:px-3"
              style={{ color: 'var(--text-muted)' }}
            >
              <RefreshCw className={`w-4 h-4 sm:mr-2 ${isLoadingJobs ? 'animate-spin' : ''}`} />
              <span className="hidden sm:inline">Refresh</span>
            </Button>
            <AuthStatus />
            {mounted && (
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={toggleTheme}
                      className="min-h-[40px] px-2 sm:px-3"
                      style={{ color: 'var(--text-muted)' }}
                    >
                      {isDarkMode ? (
                        <Sun className="w-4 h-4" />
                      ) : (
                        <Moon className="w-4 h-4" />
                      )}
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent side="bottom">
                    <p>{isDarkMode ? "Switch to light mode" : "Switch to dark mode"}</p>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            )}
          </div>
        </div>
      </header>

      <main className="px-4 pt-24 pb-8 space-y-6">
        {/* Push notification prompt - shows once when appropriate */}
        <PushNotificationPrompt />

        <div className="grid lg:grid-cols-2 gap-8">
          {/* Submit Job Card */}
          <Card className="backdrop-blur min-w-0" style={{ borderColor: 'var(--card-border)', backgroundColor: 'var(--card)' }}>
            <CardHeader>
              <CardTitle style={{ color: 'var(--text)' }}>Create Karaoke Video</CardTitle>
              <CardDescription style={{ color: 'var(--text-muted)' }}>
                Upload an audio file, provide a YouTube URL, or search for audio by artist & title
              </CardDescription>
            </CardHeader>
            <CardContent>
              <JobSubmission onJobCreated={loadJobs} />
            </CardContent>
          </Card>

          {/* Jobs List Card */}
          <Card className="backdrop-blur min-w-0" style={{ borderColor: 'var(--card-border)', backgroundColor: 'var(--card)' }}>
            <CardHeader className="px-3 sm:px-6">
              <div className="flex items-center justify-between gap-2">
                <CardTitle style={{ color: 'var(--text)' }}>Recent Jobs</CardTitle>
                <div className="flex items-center gap-2">
                  {isLoadingJobs && <Loader2 className="w-4 h-4 animate-spin" style={{ color: 'var(--text-muted)' }} />}
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          variant="ghost"
                          size="sm"
                          className="h-7 w-7 p-0"
                          style={{ color: 'var(--text-muted)' }}
                          onClick={() => {
                            const next = !hideCompleted
                            setHideCompleted(next)
                            localStorage.setItem("nomad-karaoke-hide-completed", String(next))
                          }}
                        >
                          {hideCompleted ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent side="bottom">
                        <p>{hideCompleted ? "Show all jobs" : "Hide completed jobs"}</p>
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                  <Select value={String(jobLimit)} onValueChange={(v) => {
                    const val = Number(v)
                    setJobLimit(val)
                    localStorage.setItem("nomad-karaoke-job-limit", String(val))
                  }}>
                    <SelectTrigger className="h-7 w-[80px] text-xs">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="5">5</SelectItem>
                      <SelectItem value="10">10</SelectItem>
                      <SelectItem value="20">20</SelectItem>
                      <SelectItem value="-1">All</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <CardDescription style={{ color: 'var(--text-muted)' }}>
                {hideCompleted
                  ? `${jobs.length} incomplete of ${totalFetched} total`
                  : jobs.length < totalFetched
                    ? `Showing ${jobs.length} of ${totalFetched} jobs`
                    : `${jobs.length} job${jobs.length !== 1 ? 's' : ''}`}
              </CardDescription>
            </CardHeader>
            <CardContent className="px-3 sm:px-6">
              {jobs.length === 0 ? (
                <div className="text-center py-8" style={{ color: 'var(--text-muted)' }}>
                  <Music2 className="w-12 h-12 mx-auto mb-3 opacity-50" />
                  <p>No jobs yet. Create one to get started!</p>
                </div>
              ) : (
                <div className="space-y-3">
                  {jobs.map((job) => (
                    <JobCard key={job.job_id} job={job} onRefresh={loadJobs} />
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </main>

      <VersionFooter />
    </div>
  )
}

// Wrap with Suspense for useSearchParams
export default function AppPage() {
  return (
    <Suspense fallback={
      <div className="min-h-screen flex items-center justify-center animated-gradient">
        <Loader2 className="w-8 h-8 animate-spin" style={{ color: 'var(--text-muted)' }} />
      </div>
    }>
      <AppPageContent />
    </Suspense>
  )
}
