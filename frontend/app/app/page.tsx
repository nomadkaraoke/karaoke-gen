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
import { Music2, RefreshCw, Loader2, Moon, Sun, Search, Gift, X, Shield, ShieldOff, HelpCircle, Mail, Phone } from "lucide-react"
import { sortJobsByDate } from "@/lib/job-status"
import { WarmingUpLoader } from "@/components/WarmingUpLoader"
import { JobCard } from "@/components/job"
import { GuidedJobFlow } from "@/components/job/GuidedJobFlow"
import { TenantJobFlow } from "@/components/job/TenantJobFlow"
import { TenantLogo } from "@/components/tenant-logo"
import { AuthStatus } from "@/components/auth"
import { AutoProcessor } from "@/components/AutoProcessor"
import { VersionFooter } from "@/components/version-footer"
import { PushNotificationPrompt } from "@/components/push-notification-prompt"
import { FeedbackDialog } from "@/components/feedback/FeedbackDialog"
import { useTheme } from "@/lib/theme"
import { useTenant } from "@/lib/tenant"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

function AppPageContent() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const [allJobs, setAllJobs] = useState<Job[]>([])
  const [isLoadingJobs, setIsLoadingJobs] = useState(true)
  const [isInitialLoad, setIsInitialLoad] = useState(true)
  const [hasFetchedSuccessfully, setHasFetchedSuccessfully] = useState(false)
  const [isAuthenticated, setIsAuthenticated] = useState<boolean | null>(null)
  const [isVerifyingToken, setIsVerifyingToken] = useState(false)
  const adminTokenHandled = useRef(false) // Track if admin_token was already processed
  const { isDarkMode, toggleTheme, mounted } = useTheme()
  const { user, fetchUser, verifyMagicLink } = useAuth()
  const { isDefault: isDefaultTenant, branding } = useTenant()
  const { showTestData } = useAdminSettings()
  const [jobLimit, setJobLimit] = useState<number>(() => {
    if (typeof window === "undefined") return 10
    const saved = localStorage.getItem("nomad-karaoke-job-limit")
    return saved ? Number(saved) : 10
  })
  const [statusFilter, setStatusFilter] = useState<string>(() => {
    if (typeof window === "undefined") return "all"
    return localStorage.getItem("nomad-karaoke-status-filter") || "all"
  })
  const [searchInput, setSearchInput] = useState("")
  const [searchQuery, setSearchQuery] = useState("")
  const [showAdminControls, setShowAdminControls] = useState<boolean>(() => {
    if (typeof window === "undefined") return false
    return localStorage.getItem("nomad-karaoke-admin-controls") === "true"
  })
  const [feedbackBannerDismissed, setFeedbackBannerDismissed] = useState(() => {
    if (typeof window === "undefined") return false
    return localStorage.getItem("nomad-feedback-banner-dismissed") === "true"
  })
  const [showFeedbackDialog, setShowFeedbackDialog] = useState(false)

  // Check if user is admin (for exclude_test parameter)
  const isAdmin = user?.role === "admin" || user?.email?.endsWith("@nomadkaraoke.com")

  // Filter out in-progress search jobs (managed by the guided flow, not standalone cards)
  const jobs = useMemo(
    () => allJobs.filter(job => job.status !== 'awaiting_audio_selection'),
    [allJobs]
  )

  // Debounce search input — only update the query (which triggers API calls) after 300ms
  useEffect(() => {
    const timer = setTimeout(() => setSearchQuery(searchInput), 300)
    return () => clearTimeout(timer)
  }, [searchInput])

  // Memoize loadJobs for use with visibility refresh
  const loadJobs = useCallback(async () => {
    if (!getAccessToken()) {
      setIsLoadingJobs(false)
      setIsInitialLoad(false)
      return
    }
    try {
      const data = await api.listJobs({
        limit: jobLimit,
        exclude_test: isAdmin ? !showTestData : undefined,
        fields: 'summary',
        status: statusFilter !== 'all' && statusFilter !== 'active' ? statusFilter : undefined,
        hide_completed: statusFilter === 'active' ? true : undefined,
        search: searchQuery || undefined,
      })
      setAllJobs(sortJobsByDate(data))
    } catch (err: any) {
      if (err?.status !== 401) {
        console.error("Failed to load jobs:", err)
      }
    } finally {
      setIsLoadingJobs(false)
      setIsInitialLoad(false)
      setHasFetchedSuccessfully(true)
    }
  }, [isAdmin, showTestData, jobLimit, statusFilter, searchQuery])

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
            {isDefaultTenant ? (
              <img src="/nomad-karaoke-logo.svg" alt="Nomad Karaoke" className="h-8 sm:h-10 shrink-0" />
            ) : (
              <TenantLogo size="sm" />
            )}
            <div className="min-w-0">
              <h1 className="text-base sm:text-xl font-bold truncate" style={{ color: 'var(--text)' }}>
                {isDefaultTenant ? "Karaoke Generator" : branding.site_title}
              </h1>
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
            {isAdmin && (
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        const next = !showAdminControls
                        setShowAdminControls(next)
                        localStorage.setItem("nomad-karaoke-admin-controls", String(next))
                      }}
                      className={`min-h-[40px] px-2 sm:px-3 ${showAdminControls ? 'text-amber-400' : ''}`}
                      style={showAdminControls ? undefined : { color: 'var(--text-muted)' }}
                    >
                      {showAdminControls ? <Shield className="w-4 h-4" /> : <ShieldOff className="w-4 h-4" />}
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent side="bottom">
                    <p>{showAdminControls ? "Hide admin controls" : "Show admin controls"}</p>
                  </TooltipContent>
                </Tooltip>
              </TooltipProvider>
            )}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  size="sm"
                  className="min-h-[40px] px-2 sm:px-3"
                  style={{ color: 'var(--text-muted)' }}
                >
                  <HelpCircle className="w-4 h-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-64">
                <DropdownMenuLabel className="text-sm font-normal">
                  Something confusing or not working? Reach out anytime!
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem asChild>
                  <a href="mailto:andrew@nomadkaraoke.com">
                    <Mail className="w-4 h-4 mr-2" />
                    andrew@nomadkaraoke.com
                  </a>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <a href="tel:+18036363267">
                    <Phone className="w-4 h-4 mr-2" />
                    +1 (803) 636-3267
                  </a>
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
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

      {/* Feedback dialog */}
      <FeedbackDialog
        open={showFeedbackDialog}
        onClose={() => setShowFeedbackDialog(false)}
      />

      <main className="px-4 pt-24 pb-8 space-y-6">
        {/* Push notification prompt - shows once when appropriate */}
        <PushNotificationPrompt />

        {/* Feedback-for-credits banner (consumer portal only) */}
        {isDefaultTenant && user?.feedback_eligible && !feedbackBannerDismissed && (
          <div className="flex items-center justify-between gap-3 rounded-lg border px-4 py-3"
            style={{
              borderColor: 'var(--accent)',
              backgroundColor: 'rgba(34, 197, 94, 0.05)',
            }}
          >
            <div className="flex items-center gap-3 min-w-0">
              <Gift className="w-5 h-5 text-green-500 shrink-0" />
              <p className="text-sm" style={{ color: 'var(--text)' }}>
                <strong>Earn 1 free credit!</strong>{' '}
                <span style={{ color: 'var(--text-muted)' }}>
                  Share your feedback on the karaoke creation experience.
                </span>
              </p>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <Button
                size="sm"
                variant="outline"
                onClick={() => setShowFeedbackDialog(true)}
                className="text-green-500 border-green-500/30 hover:bg-green-500/10"
              >
                Give Feedback
              </Button>
              <button
                onClick={() => {
                  localStorage.setItem("nomad-feedback-banner-dismissed", "true")
                  setFeedbackBannerDismissed(true)
                }}
                className="p-1 rounded hover:bg-secondary transition-colors"
                style={{ color: 'var(--text-muted)' }}
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}

        <div className="grid lg:grid-cols-2 gap-8">
          {/* Submit Job Card */}
          <Card className="backdrop-blur min-w-0" style={{ borderColor: 'var(--card-border)', backgroundColor: 'var(--card)' }}>
            <CardHeader>
              <CardTitle style={{ color: 'var(--text)' }}>
                {isDefaultTenant ? "Create Karaoke Video" : "Submit Track"}
              </CardTitle>
              <CardDescription style={{ color: 'var(--text-muted)' }}>
                {isDefaultTenant
                  ? "Turn any song into a karaoke video with synced lyrics"
                  : "Upload your mixed audio and instrumental"}
              </CardDescription>
            </CardHeader>
            <CardContent>
              {isDefaultTenant ? (
                <GuidedJobFlow onJobCreated={loadJobs} />
              ) : (
                <TenantJobFlow onJobCreated={loadJobs} />
              )}
            </CardContent>
          </Card>

          {/* Jobs List Card */}
          <Card className="backdrop-blur min-w-0" style={{ borderColor: 'var(--card-border)', backgroundColor: 'var(--card)' }}>
            <CardHeader className="px-3 sm:px-6">
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <div className="flex items-center gap-2">
                  <CardTitle style={{ color: 'var(--text)' }}>Recent Jobs</CardTitle>
                  {isLoadingJobs && <Loader2 className="w-4 h-4 animate-spin" style={{ color: 'var(--text-muted)' }} />}
                </div>
                <div className="flex items-center gap-2 flex-wrap">
                  <div className="relative">
                    <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5" style={{ color: 'var(--text-muted)' }} />
                    <input
                      type="text"
                      placeholder="Search..."
                      aria-label="Search jobs"
                      value={searchInput}
                      onChange={(e) => setSearchInput(e.target.value)}
                      className="h-7 w-[160px] sm:w-[200px] text-xs rounded-md border pl-7 pr-2"
                      style={{
                        backgroundColor: 'var(--input)',
                        borderColor: 'var(--border)',
                        color: 'var(--text)',
                      }}
                    />
                  </div>
                  <Select value={statusFilter} onValueChange={(v) => {
                    setStatusFilter(v)
                    localStorage.setItem("nomad-karaoke-status-filter", v)
                  }}>
                    <SelectTrigger className="h-7 w-[130px] text-xs" aria-label="Filter by status">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">All statuses</SelectItem>
                      <SelectItem value="active">Active</SelectItem>
                      <SelectItem value="failed">Failed</SelectItem>
                      <SelectItem value="complete">Completed</SelectItem>
                      <SelectItem value="cancelled">Cancelled</SelectItem>
                      <SelectItem value="awaiting_review">Awaiting review</SelectItem>
                      <SelectItem value="processing">Processing</SelectItem>
                    </SelectContent>
                  </Select>
                  <Select value={String(jobLimit)} onValueChange={(v) => {
                    const val = Number(v)
                    setJobLimit(val)
                    localStorage.setItem("nomad-karaoke-job-limit", String(val))
                  }}>
                    <SelectTrigger className="h-7 w-[70px] text-xs" aria-label="Results per page">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="5">5</SelectItem>
                      <SelectItem value="10">10</SelectItem>
                      <SelectItem value="20">20</SelectItem>
                      <SelectItem value="50">50</SelectItem>
                      <SelectItem value="100">100</SelectItem>
                      <SelectItem value="1000">1000</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <CardDescription style={{ color: 'var(--text-muted)' }}>
                {!hasFetchedSuccessfully
                  ? 'Loading...'
                  : searchQuery || (statusFilter !== 'all' && statusFilter !== 'active')
                    ? `${jobs.length} job${jobs.length !== 1 ? 's' : ''} matching filters`
                    : `${jobs.length} job${jobs.length !== 1 ? 's' : ''}`}
              </CardDescription>
            </CardHeader>
            <CardContent className="px-3 sm:px-6">
              {jobs.length === 0 ? (
                <div className="text-center py-8" style={{ color: 'var(--text-muted)' }}>
                  {!hasFetchedSuccessfully ? (
                    <>
                      <Loader2 className="w-12 h-12 mx-auto mb-3 opacity-50 animate-spin" />
                      <p>Loading jobs...</p>
                    </>
                  ) : searchQuery || (statusFilter !== 'all' && statusFilter !== 'active') ? (
                    <>
                      <Search className="w-12 h-12 mx-auto mb-3 opacity-50" />
                      <p>No jobs match your filters.</p>
                    </>
                  ) : (
                    <>
                      <Music2 className="w-12 h-12 mx-auto mb-3 opacity-50" />
                      <p>No jobs yet. Create one to get started!</p>
                    </>
                  )}
                </div>
              ) : (
                <div className="space-y-3">
                  {jobs.map((job) => (
                    <JobCard key={job.job_id} job={job} onRefresh={loadJobs} showAdminControls={isAdmin && showAdminControls} />
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
