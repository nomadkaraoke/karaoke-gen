"use client"

import { useState, useEffect, useCallback, useMemo, Suspense, useRef } from "react"
import { useRouter, Link } from "@/i18n/routing"
import { useSearchParams } from "next/navigation"
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
import { Music2, RefreshCw, Loader2, Search, Gift, X, Shield, ShieldOff } from "lucide-react"
import { useTranslations } from "next-intl"
import { sortJobsByDate } from "@/lib/job-status"
import { WarmingUpLoader } from "@/components/WarmingUpLoader"
import { JobCard } from "@/components/job"
import { GuidedJobFlow } from "@/components/job/GuidedJobFlow"
import { TenantJobFlow } from "@/components/job/TenantJobFlow"
import { AutoProcessor } from "@/components/AutoProcessor"
import { VersionFooter } from "@/components/version-footer"
import { PushNotificationPrompt } from "@/components/push-notification-prompt"
import { FeedbackDialog } from "@/components/feedback/FeedbackDialog"
import { AppHeader } from "@/components/app-header"
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
  const t = useTranslations('dashboard')
  const router = useRouter()
  const searchParams = useSearchParams()
  const [allJobs, setAllJobs] = useState<Job[]>([])
  const [isLoadingJobs, setIsLoadingJobs] = useState(true)
  const [isInitialLoad, setIsInitialLoad] = useState(true)
  const [hasFetchedSuccessfully, setHasFetchedSuccessfully] = useState(false)
  const [isAuthenticated, setIsAuthenticated] = useState<boolean | null>(null)
  const [isVerifyingToken, setIsVerifyingToken] = useState(false)
  const adminTokenHandled = useRef(false) // Track if admin_token was already processed
  const { user, fetchUser, verifyMagicLink } = useAuth()
  const { isDefault: isDefaultTenant } = useTenant()
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
  const [discountBannerDismissed, setDiscountBannerDismissed] = useState(() => {
    if (typeof window === "undefined") return false
    return localStorage.getItem("nomad-discount-banner-dismissed") === "true"
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
      <AppHeader>
        <Button
          variant="ghost"
          size="sm"
          onClick={loadJobs}
          disabled={isLoadingJobs || !isAuthenticated}
          className="min-h-[40px] px-2 sm:px-3"
          style={{ color: 'var(--text-muted)' }}
        >
          <RefreshCw className={`w-4 h-4 sm:me-2 ${isLoadingJobs ? 'animate-spin' : ''}`} />
          <span className="hidden sm:inline">{t('refresh')}</span>
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
                <p>{showAdminControls ? t('hideAdminControls') : t('showAdminControls')}</p>
              </TooltipContent>
            </Tooltip>
          </TooltipProvider>
        )}
      </AppHeader>

      {/* Feedback dialog */}
      <FeedbackDialog
        open={showFeedbackDialog}
        onClose={() => setShowFeedbackDialog(false)}
      />

      <main className="px-4 pt-24 pb-8 space-y-6">
        {/* Push notification prompt - shows once when appropriate */}
        <PushNotificationPrompt />

        {/* Referral discount banner */}
        {user?.has_active_referral_discount && user?.referral_discount_percent && user?.referral_discount_expires_at && !discountBannerDismissed && (
          <div
            className="rounded-xl p-4 flex items-center gap-3 border"
            style={{
              borderColor: 'var(--accent)',
              backgroundColor: 'rgba(168, 85, 247, 0.05)',
            }}
          >
            <Gift className="w-5 h-5 shrink-0" style={{ color: 'var(--accent)' }} />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium" style={{ color: 'var(--text)' }}>
                {user.referral_discount_percent}% referral discount active
              </p>
              <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
                {user.referred_by_code && <>Referred with code <strong>{user.referred_by_code}</strong> · </>}
                Expires {new Date(user.referral_discount_expires_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
              </p>
            </div>
            <button
              onClick={() => {
                localStorage.setItem("nomad-discount-banner-dismissed", "true")
                setDiscountBannerDismissed(true)
              }}
              className="shrink-0 p-1 rounded-md hover:bg-white/10 transition-colors"
              style={{ color: 'var(--text-muted)' }}
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        )}

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
                <strong>{t('earnFreeCredit')}</strong>{' '}
                <span style={{ color: 'var(--text-muted)' }}>
                  {t('feedbackPrompt')}
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
                {t('giveFeedback')}
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
                {isDefaultTenant ? t('createKaraokeVideo') : t('submitTrack')}
              </CardTitle>
              <CardDescription style={{ color: 'var(--text-muted)' }}>
                {isDefaultTenant
                  ? t('createDescription')
                  : t('submitDescription')}
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
                  <CardTitle style={{ color: 'var(--text)' }}>{t('recentJobs')}</CardTitle>
                  {isLoadingJobs && <Loader2 className="w-4 h-4 animate-spin" style={{ color: 'var(--text-muted)' }} />}
                </div>
                <div className="flex items-center gap-2 flex-wrap">
                  <div className="relative">
                    <Search className="absolute start-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5" style={{ color: 'var(--text-muted)' }} />
                    <input
                      type="text"
                      placeholder={t('searchPlaceholder')}
                      aria-label={t('searchPlaceholder')}
                      value={searchInput}
                      onChange={(e) => setSearchInput(e.target.value)}
                      className="h-7 w-[160px] sm:w-[200px] text-xs rounded-md border ps-7 pe-2"
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
                    <SelectTrigger className="h-7 w-[130px] text-xs" aria-label={t('filterByStatus')}>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">{t('allStatuses')}</SelectItem>
                      <SelectItem value="active">{t('active')}</SelectItem>
                      <SelectItem value="failed">{t('failed')}</SelectItem>
                      <SelectItem value="complete">{t('completed')}</SelectItem>
                      <SelectItem value="cancelled">{t('cancelled')}</SelectItem>
                      <SelectItem value="awaiting_review">{t('awaitingReview')}</SelectItem>
                      <SelectItem value="processing">{t('processing')}</SelectItem>
                    </SelectContent>
                  </Select>
                  <Select value={String(jobLimit)} onValueChange={(v) => {
                    const val = Number(v)
                    setJobLimit(val)
                    localStorage.setItem("nomad-karaoke-job-limit", String(val))
                  }}>
                    <SelectTrigger className="h-7 w-[70px] text-xs" aria-label={t('resultsPerPage')}>
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
                  ? t('loadingJobs')
                  : searchQuery || (statusFilter !== 'all' && statusFilter !== 'active')
                    ? t('jobsMatchingFilters', { count: jobs.length })
                    : t('jobsCount', { count: jobs.length })}
              </CardDescription>
            </CardHeader>
            <CardContent className="px-3 sm:px-6">
              {jobs.length === 0 ? (
                <div className="text-center py-8" style={{ color: 'var(--text-muted)' }}>
                  {!hasFetchedSuccessfully ? (
                    <>
                      <Loader2 className="w-12 h-12 mx-auto mb-3 opacity-50 animate-spin" />
                      <p>{t('loadingJobs')}</p>
                    </>
                  ) : searchQuery || (statusFilter !== 'all' && statusFilter !== 'active') ? (
                    <>
                      <Search className="w-12 h-12 mx-auto mb-3 opacity-50" />
                      <p>{t('noJobsMatchFilters')}</p>
                    </>
                  ) : (
                    <>
                      <Music2 className="w-12 h-12 mx-auto mb-3 opacity-50" />
                      <p>{t('noJobsYet')}</p>
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
