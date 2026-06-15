"use client"

import { useTranslations } from "next-intl"
import { useState, useEffect, useMemo, useCallback, useRef } from "react"
import { api, ApiError, CatalogTrackResult, CommunityCheckResponse, MatchJudgeVerdict } from "@/lib/api"
import { CommunityVersionBanner } from "@/components/job/CommunityVersionBanner"
import {
  ExtendedAudioSearchResult,
  groupResults,
  getSearchConfidence,
  getAvailabilityLabel,
  checkFilenameMismatch,
  getDisplayName,
  formatCount,
  formatMetadata,
  formatQuality,
  SearchConfidence,
} from "@/lib/audio-search-utils"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Loader2, ChevronDown, ChevronUp, Upload, Youtube, ArrowLeft, Check, AlertTriangle, CheckCircle2, Lightbulb, Info, X, Scissors, Zap } from "lucide-react"
import { BuyCreditsDialog } from "@/components/credits/BuyCreditsDialog"

interface AudioSourceStepProps {
  artist: string
  title: string
  onSearchCompleted: (searchSessionId: string) => void
  onSearchResultChosen: (resultIndex: number) => void
  onArtistTitleCorrection: (artist: string, title: string) => void
  onUrlReady: (url: string) => void
  onFileReady: (file: File) => void
  onBack: () => void
  noCredits: boolean
  onAudioEditChange: (value: boolean) => void
}

type PendingChoice =
  | { type: "search"; index: number }
  | { type: "url"; url: string }
  | { type: "file"; file: File }

type SearchStatus =
  | { phase: 'searching'; attempt: number }
  | { phase: 'succeeded' }
  | { phase: 'failed'; reason: string; isCreditError: boolean; attempt: number }

const MAX_SEARCH_ATTEMPTS = 3
const RETRY_DELAYS = [0, 2000, 4000] // ms delay before each attempt

/** Returns true for transient errors that should be auto-retried */
function isRetryableError(err: unknown): boolean {
  // Network errors (TypeError: Failed to fetch, connection refused, CORS)
  if (!(err instanceof ApiError)) return true
  // Server errors (5xx) are retryable
  if (err.status >= 500) return true
  // Everything else (4xx) is not
  return false
}

/** Render an availability badge with tooltip */
function AvailabilityBadge({ seeders }: { seeders: number | undefined | null }) {
  if (seeders === undefined || seeders === null) return null
  const { text, tooltip } = getAvailabilityLabel(seeders)
  if (!text) return null

  return (
    <span
      title={tooltip}
      className={`text-[8px] px-1 py-0.5 rounded font-medium cursor-help ${
        seeders >= 50 ? 'bg-green-600/20 text-green-400' :
        seeders >= 10 ? 'bg-yellow-600/20 text-yellow-400' :
        'bg-red-600/20 text-red-400'
      }`}
    >
      {text} avail.
    </span>
  )
}

/** Render a color-coded view count badge */
function ViewCountBadge({ viewCount }: { viewCount: number | undefined | null }) {
  if (viewCount === undefined || viewCount === null) return null

  const color = viewCount >= 1_000_000 ? 'bg-green-600/20 text-green-400' :
    viewCount >= 100_000 ? 'bg-yellow-600/20 text-yellow-400' :
    'bg-[var(--secondary)] text-[var(--text-muted)]'

  const tooltip = viewCount >= 1_000_000 ? 'Very popular — likely the right track' :
    viewCount >= 100_000 ? 'Moderately popular' :
    'Low view count — verify this is the right track'

  return (
    <span
      title={tooltip}
      className={`text-[8px] px-1 py-0.5 rounded font-medium cursor-help ${color}`}
    >
      {formatCount(viewCount)} views
    </span>
  )
}

/** Render a filename mismatch badge */
function MismatchBadge({ searchTitle, result }: { searchTitle: string; result: ExtendedAudioSearchResult }) {
  const mismatch = useMemo(() => checkFilenameMismatch(searchTitle, result), [searchTitle, result])
  if (!mismatch.isMismatch) return null

  return (
    <span
      title={`Expected "${searchTitle}" but filename is "${mismatch.filename}"${mismatch.suggestedTrack ? ` (looks like "${mismatch.suggestedTrack}")` : ''}`}
      className="text-[8px] px-1 py-0.5 rounded font-medium bg-yellow-600/20 text-yellow-400 cursor-help"
    >
      Wrong track?
    </span>
  )
}

export function AudioSourceStep({
  artist,
  title,
  onSearchCompleted,
  onSearchResultChosen,
  onArtistTitleCorrection,
  onUrlReady,
  onFileReady,
  onBack,
  noCredits,
  onAudioEditChange,
}: AudioSourceStepProps) {
  const [results, setResults] = useState<ExtendedAudioSearchResult[]>([])
  const [searchStatus, setSearchStatus] = useState<SearchStatus>({ phase: 'searching', attempt: 1 })
  const [pendingChoice, setPendingChoice] = useState<PendingChoice | null>(null)

  // Derived from searchStatus for rendering convenience
  const isSearching = searchStatus.phase === 'searching'
  const error = searchStatus.phase === 'failed' ? searchStatus.reason : ''
  const isCreditError = searchStatus.phase === 'failed' && searchStatus.isCreditError
  const [showOtherOptions, setShowOtherOptions] = useState(false)
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set())
  const searchTriggered = useRef(false)

  // Community version check (parallel to audio search)
  const [communityData, setCommunityData] = useState<CommunityCheckResponse | null>(null)
  const [communityDismissed, setCommunityDismissed] = useState(false)

  // Artist/title match judge: formatting tidy + match-acceptance + did-you-mean.
  // Runs after the audio search resolves so the judge sees the confidence tier.
  const [verdict, setVerdict] = useState<MatchJudgeVerdict | null>(null)
  const [verdictDismissed, setVerdictDismissed] = useState(false)
  // What we auto-applied, so the user can Undo back to exactly what they typed.
  const [appliedFrom, setAppliedFrom] = useState<{ artist: string; title: string } | null>(null)
  const judgeTriggered = useRef(false)

  const [showBuyCreditsDialog, setShowBuyCreditsDialog] = useState(false)

  // Fallback form state
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [youtubeUrl, setYoutubeUrl] = useState("")
  const [fallbackMode, setFallbackMode] = useState<"upload" | "url" | null>(null)

  // Search with automatic retry for transient failures
  const doSearch = useCallback(async (attempt = 1): Promise<void> => {
    setSearchStatus({ phase: 'searching', attempt })

    // Delay before retry attempts
    if (attempt > 1) {
      await new Promise(r => setTimeout(r, RETRY_DELAYS[attempt - 1] ?? RETRY_DELAYS[RETRY_DELAYS.length - 1]))
    }

    try {
      const response = await api.searchStandalone(artist, title)
      onSearchCompleted(response.search_session_id)
      setResults(response.results as ExtendedAudioSearchResult[])
      setSearchStatus({ phase: 'succeeded' })
    } catch (err) {
      // Credit errors — never retry
      if (err instanceof ApiError && err.status === 402) {
        setSearchStatus({
          phase: 'failed',
          reason: "You're out of credits. Buy more to continue creating karaoke videos.",
          isCreditError: true,
          attempt,
        })
        return
      }

      // Retryable errors — try again if attempts remain
      if (isRetryableError(err) && attempt < MAX_SEARCH_ATTEMPTS) {
        return doSearch(attempt + 1)
      }

      // All retries exhausted or non-retryable error — show failure
      let reason: string
      if (err instanceof ApiError) {
        reason = err.message
      } else if (err instanceof DOMException && err.name === 'AbortError') {
        reason = "Search timed out. Please try again."
      } else {
        reason = "Search failed due to a network error. Please try again."
      }

      setSearchStatus({ phase: 'failed', reason, isCreditError: false, attempt })
    }
  }, [artist, title, onSearchCompleted])

  // Auto-trigger audio search + community check in parallel on mount.
  // The match judge runs in a separate effect once the search resolves (below).
  useEffect(() => {
    if (searchTriggered.current) return
    searchTriggered.current = true
    doSearch()
    // Fire community version check in parallel (non-blocking)
    api.checkCommunityVersions(artist, title)
      .then((data) => setCommunityData(data))
      .catch(() => {
        // Silently fail — community check is a nice-to-have
      })
  }, [doSearch, artist, title])

  const confidence = useMemo(() => getSearchConfidence(results, title), [results, title])
  const groupedResults = useMemo(() => groupResults(results), [results])
  const otherResultsCount = results.length > 0 && confidence.bestResult ? results.length - 1 : 0

  // Match judge: runs once the audio search resolves (so it sees the confidence
  // tier). Deterministic+catalog formatting fixes apply silently; the light AI
  // layer only fires server-side when those aren't confident.
  useEffect(() => {
    if (judgeTriggered.current) return
    if (isSearching) return // Wait for the audio search so the tier is known
    judgeTriggered.current = true
    api.matchJudge(artist, title, confidence.tier)
      .then((v) => {
        setVerdict(v)
        if (v.kind === 'cosmetic' && v.confident) {
          // Same song, formatting only — apply silently, never re-search.
          applyCorrection(v.canonical_artist, v.canonical_title, false)
        } else if (v.kind === 'content' && v.confident) {
          // Real character change — apply, and re-search only if the typo'd
          // search came back weak (tier 3) so we fetch better audio for the fix.
          applyCorrection(v.canonical_artist, v.canonical_title, confidence.tier >= 3)
        }
        // ambiguous / unconfident content / none → render an ask (no auto-apply)
      })
      .catch(() => {
        // Silently fail — matching is a nice-to-have
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isSearching, artist, title])

  // Apply a corrected artist/title. reSearch=true re-runs the audio search with
  // the corrected terms; the verdict notice stays visible across the re-search.
  function applyCorrection(newArtist: string, newTitle: string, reSearch: boolean) {
    if (newArtist === artist && newTitle === title) return
    setAppliedFrom({ artist, title })
    onArtistTitleCorrection(newArtist, newTitle)
    if (reSearch) {
      setResults([])
      setSearchStatus({ phase: 'searching', attempt: 1 })
      setCommunityData(null)
      setCommunityDismissed(false)
      setShowOtherOptions(false)
      // Keep judgeTriggered=true: don't re-judge the corrected terms. doSearch
      // re-fires via the mount effect when the parent re-renders with the
      // corrected artist/title props (searchTriggered is reset below).
      searchTriggered.current = false
    }
  }

  // User accepts an ambiguous "Did you mean?" suggestion (always re-search).
  function handleSuggestionAccept(newArtist: string, newTitle: string) {
    setVerdict({
      kind: 'content', confident: true,
      canonical_artist: newArtist, canonical_title: newTitle,
      alternatives: [], engine: 'ai', reason: '',
    })
    applyCorrection(newArtist, newTitle, true)
  }

  // User undoes an auto-applied correction — restore exactly what they typed.
  function handleUndoCorrection() {
    if (!appliedFrom) return
    const prev = appliedFrom
    setAppliedFrom(null)
    setVerdict(null)
    onArtistTitleCorrection(prev.artist, prev.title)
  }

  function handleSelect(index: number) {
    // Show audio edit question before advancing
    setPendingChoice({ type: "search", index })
  }

  function handleBack() {
    onBack()
  }

  function toggleCategory(category: string) {
    setExpandedCategories(prev => {
      const next = new Set(prev)
      if (next.has(category)) {
        next.delete(category)
      } else {
        next.add(category)
      }
      return next
    })
  }

  function handleUploadSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!uploadFile) return
    setPendingChoice({ type: "file", file: uploadFile })
  }

  function handleUrlSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!youtubeUrl.trim()) return
    setPendingChoice({ type: "url", url: youtubeUrl.trim() })
  }

  function handleAudioEditAnswer(wantsEdit: boolean) {
    if (!pendingChoice) return
    onAudioEditChange(wantsEdit)
    // Now advance to the next step
    if (pendingChoice.type === "search") {
      onSearchResultChosen(pendingChoice.index)
    } else if (pendingChoice.type === "url") {
      onUrlReady(pendingChoice.url)
    } else if (pendingChoice.type === "file") {
      onFileReady(pendingChoice.file)
    }
  }

  const handleRetry = useCallback(() => {
    searchTriggered.current = false
    doSearch()
  }, [doSearch])

  const isLossyBest = confidence.bestCategory === 'YOUTUBE'

  // Audio edit question — shown after user selects audio, before advancing
  if (pendingChoice) {
    return (
      <div className="space-y-5">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
              Audio selected for <span className="font-medium" style={{ color: 'var(--text)' }}>{artist} - {title}</span>
            </p>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setPendingChoice(null)}
            style={{ color: 'var(--text-muted)' }}
          >
            <ArrowLeft className="w-4 h-4 mr-1" />
            Back
          </Button>
        </div>

        <div className="flex flex-col gap-3">
          {/* Default: use as-is (recommended for most users) */}
          <button
            type="button"
            onClick={() => handleAudioEditAnswer(false)}
            className="group rounded-lg border-2 p-4 transition-all text-left flex items-start gap-4 border-[var(--success)] hover:bg-[rgba(34,197,94,0.06)]"
            style={{ backgroundColor: 'rgba(34, 197, 94, 0.03)' }}
          >
            <div className="mt-0.5 flex-shrink-0 rounded-full p-1.5" style={{ backgroundColor: 'rgba(34, 197, 94, 0.15)' }}>
              <Zap className="w-5 h-5" style={{ color: 'var(--success)' }} />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-semibold" style={{ color: 'var(--text)' }}>Use audio as-is</span>
                <span className="text-[10px] font-medium uppercase tracking-wide px-1.5 py-0.5 rounded" style={{ backgroundColor: 'rgba(34, 197, 94, 0.15)', color: 'var(--success)' }}>
                  Recommended
                </span>
              </div>
              <p className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>
                Start processing right away — best for most tracks
              </p>
            </div>
          </button>

          {/* Optional: edit first */}
          <button
            type="button"
            onClick={() => handleAudioEditAnswer(true)}
            className="group rounded-lg border-2 p-4 transition-all text-left flex items-start gap-4 border-[var(--card-border)] hover:border-[var(--brand-purple)]"
          >
            <div className="mt-0.5 flex-shrink-0 rounded-full p-1.5" style={{ backgroundColor: 'rgba(139, 92, 246, 0.12)' }}>
              <Scissors className="w-5 h-5" style={{ color: 'var(--brand-purple)' }} />
            </div>
            <div className="flex-1 min-w-0">
              <span className="font-semibold" style={{ color: 'var(--text)' }}>Edit audio first</span>
              <p className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>
                Trim, cut, or mute sections before processing
              </p>
              <p className="text-xs mt-2 flex items-start gap-1.5" style={{ color: 'var(--text-muted)' }}>
                <Lightbulb className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" style={{ color: 'var(--brand-gold)' }} />
                <span>Great for live recordings with intros to skip, or tracks with sections you want to remove</span>
              </p>
            </div>
          </button>
        </div>
      </div>
    )
  }

  return (
    <>
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
            Searching for <span className="font-medium" style={{ color: 'var(--text)' }}>{artist} - {title}</span>
          </p>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={handleBack}
          style={{ color: 'var(--text-muted)' }}
        >
          <ArrowLeft className="w-4 h-4 mr-1" />
          Back
        </Button>
      </div>

      {/* Artist/title formatting tidy + did-you-mean */}
      {verdict && !verdictDismissed && (
        <MatchNotice
          verdict={verdict}
          appliedFrom={appliedFrom}
          onAccept={handleSuggestionAccept}
          onUndo={handleUndoCorrection}
          onDismiss={() => setVerdictDismissed(true)}
        />
      )}

      {/* Community version banner */}
      {communityData?.has_community && !communityDismissed && (
        <CommunityVersionBanner
          data={communityData}
          onDismiss={() => setCommunityDismissed(true)}
        />
      )}

      {/* Error display */}
      {error && (
        <div className="text-sm rounded p-3 text-red-400 bg-red-500/10">
          <p>{error}</p>
          {isCreditError && (
            <button onClick={() => setShowBuyCreditsDialog(true)} className="inline-block mt-1 font-medium underline" style={{ color: 'var(--brand-pink)' }}>Buy Credits</button>
          )}
          {!isCreditError && (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-1">
              <button onClick={handleRetry} className="font-medium underline" style={{ color: 'var(--brand-pink)' }}>Retry</button>
              <span className="text-xs text-muted-foreground">
                Still not working? <a href="mailto:andrew@nomadkaraoke.com" className="underline hover:text-foreground">Email andrew@nomadkaraoke.com</a>
              </span>
            </div>
          )}
        </div>
      )}

      {/* Loading state */}
      {searchStatus.phase === 'searching' && (
        <div className="flex flex-col items-center justify-center py-12 gap-3">
          <Loader2 className="w-8 h-8 animate-spin" style={{ color: 'var(--brand-pink)' }} />
          <div className="text-center">
            <p className="text-sm font-medium" style={{ color: 'var(--text)' }}>Searching for audio sources...</p>
            <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
              {searchStatus.attempt > 1
                ? `Retry ${searchStatus.attempt - 1} of ${MAX_SEARCH_ATTEMPTS - 1} — this usually takes 15-30 seconds`
                : 'This usually takes 15-30 seconds'}
            </p>
          </div>
        </div>
      )}

      {/* Tier 1: Perfect match found */}
      {!isSearching && confidence.tier === 1 && confidence.bestResult && (
        <PickCard
          confidence={confidence}
          variant="perfect"
          searchTitle={title}
          onSelect={handleSelect}
          noCredits={noCredits}
        />
      )}

      {/* Tier 2: Recommended with reasoning */}
      {!isSearching && confidence.tier === 2 && confidence.bestResult && (
        <PickCard
          confidence={confidence}
          variant="recommended"
          searchTitle={title}
          onSelect={handleSelect}
          noCredits={noCredits}
        />
      )}

      {/* Tier 3: Guidance banner + fallback FIRST, then search results below */}
      {!isSearching && confidence.tier === 3 && results.length > 0 && (
        <>
          <GuidanceBanner results={results} />
          <FallbackSection
            tier={confidence.tier}
            hasResults={results.length > 0}
            fallbackMode={fallbackMode}
            setFallbackMode={setFallbackMode}
            youtubeUrl={youtubeUrl}
            setYoutubeUrl={setYoutubeUrl}
            uploadFile={uploadFile}
            setUploadFile={setUploadFile}
            noCredits={noCredits}
            onUrlSubmit={handleUrlSubmit}
            onUploadSubmit={handleUploadSubmit}
          />
        </>
      )}

      {/* Other options section — varies by tier */}
      {!isSearching && results.length > 0 && (
        <OtherOptionsSection
          confidence={confidence}
          groupedResults={groupedResults}
          otherResultsCount={otherResultsCount}
          showOtherOptions={showOtherOptions}
          setShowOtherOptions={setShowOtherOptions}
          expandedCategories={expandedCategories}
          toggleCategory={toggleCategory}
          searchTitle={title}
          onSelect={handleSelect}
          noCredits={noCredits}
        />
      )}

      {/* No results */}
      {searchStatus.phase === 'succeeded' && results.length === 0 && (
        <NoResultsSection />
      )}

      {/* Fallback section for Tier 1/2 and no-results — at the bottom */}
      {searchStatus.phase !== 'searching' && (confidence.tier !== 3 || results.length === 0) && (
        <FallbackSection
          tier={confidence.tier}
          hasResults={results.length > 0}
          fallbackMode={fallbackMode}
          setFallbackMode={setFallbackMode}
          youtubeUrl={youtubeUrl}
          setYoutubeUrl={setYoutubeUrl}
          uploadFile={uploadFile}
          setUploadFile={setUploadFile}
          noCredits={noCredits}
          onUrlSubmit={handleUrlSubmit}
          onUploadSubmit={handleUploadSubmit}
        />
      )}
    </div>
    <BuyCreditsDialog open={showBuyCreditsDialog} onClose={() => setShowBuyCreditsDialog(false)} />
    </>
  )
}

/** Pick card for Tier 1 and Tier 2 */
function PickCard({
  confidence,
  variant,
  searchTitle,
  onSelect,
  noCredits,
}: {
  confidence: SearchConfidence
  variant: "perfect" | "recommended"
  searchTitle: string
  onSelect: (index: number) => void
  noCredits: boolean
}) {
  const bestResult = confidence.bestResult!
  const isPerfect = variant === "perfect"
  const isLossyBest = confidence.bestCategory === 'YOUTUBE'

  return (
    <div
      className={`border rounded-lg overflow-hidden ${
        isPerfect
          ? 'border-green-500/40 bg-green-500/5'
          : 'border-amber-500/40 bg-amber-500/5'
      }`}
      data-testid="pick-card"
      data-confidence-tier={confidence.tier}
    >
      <div className={`px-3 py-2 flex items-center gap-2 ${
        isPerfect ? 'bg-green-500/10' : 'bg-amber-500/10'
      }`}>
        {isPerfect ? (
          <CheckCircle2 className="w-3.5 h-3.5 text-green-400" />
        ) : null}
        <span className={`text-xs font-semibold ${isPerfect ? 'text-green-400' : 'text-amber-400'}`}>
          {isPerfect ? 'Perfect match found' : 'Recommended Audio'}
        </span>
        {bestResult.is_lossless && (
          <span className="text-[8px] px-1 py-0.5 rounded bg-green-600/20 text-green-400 font-medium">
            LOSSLESS
          </span>
        )}
        {bestResult.provider === "YouTube" && (
          <span className="text-[8px] px-1 py-0.5 rounded font-medium bg-red-600/20 text-red-400">
            YouTube
          </span>
        )}
      </div>
      <div className="px-3 py-3 space-y-2">
        {/* Confidence reason */}
        <p className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
          {confidence.reason}
        </p>

        <div className="flex items-start gap-3">
          <div className="flex-1 min-w-0">
            {bestResult.target_file && (
              <div className="flex items-baseline gap-1.5">
                <span className="text-[10px] shrink-0" style={{ color: 'var(--text-muted)' }}>Filename:</span>
                <span className="font-medium text-sm font-mono break-all" style={{ color: 'var(--text)' }}>
                  {bestResult.target_file}
                </span>
              </div>
            )}
            <div className="flex items-baseline gap-1.5 mt-0.5">
              <span className="text-[10px] shrink-0" style={{ color: 'var(--text-muted)' }}>Release:</span>
              <span className="font-medium text-sm" style={{ color: 'var(--text)' }}>
                {getDisplayName(bestResult)} - {bestResult.title}
              </span>
            </div>
            <div className="flex items-center gap-1.5 flex-wrap mt-1.5">
              <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
                {formatQuality(bestResult)}
              </span>
              <AvailabilityBadge seeders={bestResult.seeders} />
              {!bestResult.seeders && (
                <ViewCountBadge viewCount={bestResult.view_count} />
              )}
              <MismatchBadge searchTitle={searchTitle} result={bestResult} />
            </div>
            {formatMetadata(bestResult) && (
              <div className="text-[10px] mt-1" style={{ color: 'var(--text-muted)' }}>
                {formatMetadata(bestResult)}
              </div>
            )}
          </div>
        </div>

        {/* Warnings */}
        {confidence.warnings.length > 0 && (
          <div className="space-y-1">
            {confidence.warnings.map((warning, i) => (
              <div key={i} className="flex items-start gap-2 rounded p-2 text-[10px] bg-yellow-600/10 text-yellow-400">
                <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                <span>{warning}</span>
              </div>
            ))}
          </div>
        )}

        {/* Lossy quality warning (separate from confidence warnings) */}
        {isLossyBest && !confidence.warnings.some(w => w.includes('lossless')) && (
          <div className="flex items-start gap-2 rounded p-2 text-[10px] bg-yellow-600/10 text-yellow-400">
            <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
            <span>This is a YouTube/lossy source. Lossless audio produces better karaoke results when available.</span>
          </div>
        )}

        <Button
          onClick={() => onSelect(bestResult.index)}
          disabled={noCredits}
          className={`w-full text-white ${
            isPerfect
              ? 'bg-green-600 hover:bg-green-500'
              : 'bg-amber-600 hover:bg-amber-500'
          }`}
        >
          <Check className="w-4 h-4 mr-1.5" />
          Use This Audio
        </Button>
      </div>
    </div>
  )
}

/** Guidance banner for Tier 3 */
function GuidanceBanner({ results }: { results: ExtendedAudioSearchResult[] }) {
  const tips = useMemo(() => getGuidanceTips(results), [results])

  return (
    <div
      className="border rounded-lg p-3 space-y-2 border-[var(--card-border)] bg-[var(--secondary)]/30"
      data-testid="guidance-banner"
      data-confidence-tier={3}
    >
      <div className="flex items-center gap-2">
        <Lightbulb className="w-4 h-4 text-amber-400 shrink-0" />
        <span className="text-xs font-semibold" style={{ color: 'var(--text)' }}>
          Limited sources found
        </span>
      </div>
      <ul className="text-[10px] space-y-1 ml-6" style={{ color: 'var(--text-muted)' }}>
        {tips.map((tip, i) => <li key={i}>{tip}</li>)}
      </ul>
    </div>
  )
}

/** Build context-aware guidance tips based on actual result composition */
function getGuidanceTips(results: ExtendedAudioSearchResult[]): string[] {
  const hasFiles = results.some(r => r.target_file)
  const hasYoutube = results.some(r => r.provider?.toLowerCase() === 'youtube')
  const hasSpotify = results.some(r => r.provider?.toLowerCase() === 'spotify')
  const hasLossless = results.some(r => r.is_lossless && !['youtube', 'spotify'].includes(r.provider?.toLowerCase() ?? ''))
  const hasVinyl = results.some(r => r.quality_data?.media?.toLowerCase() === 'vinyl')
  const hasSeeders = results.some(r => r.seeders !== undefined && r.seeders !== null)
  const noLossless = !hasLossless

  const tips: string[] = []

  if (hasFiles) {
    tips.push('Check the filename matches your song title')
  }
  if (hasSeeders) {
    tips.push('Green availability badge = more reliable download')
  }
  if (hasLossless) {
    tips.push('Studio album versions produce the best karaoke results')
  }
  if (hasVinyl) {
    tips.push('Avoid vinyl rips (surface noise affects separation)')
  }
  if (hasYoutube && hasLossless) {
    tips.push('YouTube is a last resort — lossless sources sound better')
  }
  if (noLossless) {
    tips.push('No high quality lossless sources were found')
  }
  if (hasSpotify && hasYoutube) {
    tips.push('Spotify results are higher quality than YouTube')
  }
  if (noLossless && hasYoutube) {
    tips.push('If YouTube is the only option, official music videos or audio-only uploads tend to have the best quality')
  }

  return tips
}

/** Other options section — render differently per tier */
function OtherOptionsSection({
  confidence,
  groupedResults,
  otherResultsCount,
  showOtherOptions,
  setShowOtherOptions,
  expandedCategories,
  toggleCategory,
  searchTitle,
  onSelect,
  noCredits,
}: {
  confidence: SearchConfidence
  groupedResults: ReturnType<typeof groupResults>
  otherResultsCount: number
  showOtherOptions: boolean
  setShowOtherOptions: (show: boolean) => void
  expandedCategories: Set<string>
  toggleCategory: (category: string) => void
  searchTitle: string
  onSelect: (index: number) => void
  noCredits: boolean
}) {
  const isTier3 = confidence.tier === 3

  // For Tier 3, always show results expanded (no toggle needed)
  // For Tier 1/2, show toggle to expand other options
  if (isTier3) {
    return (
      <div className="border-t pt-4 space-y-2" style={{ borderColor: 'var(--card-border)' }}>
        <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
          Search results (may not match your song):
        </p>
        <ResultCategories
          groupedResults={groupedResults}
          bestResult={null} // Don't filter any result for Tier 3
          expandedCategories={expandedCategories}
          toggleCategory={toggleCategory}
          searchTitle={searchTitle}
          onSelect={onSelect}
          noCredits={noCredits}
          defaultExpanded
          alwaysShowMatchBadge
        />
      </div>
    )
  }

  if (otherResultsCount <= 0) return null

  return (
    <div>
      <button
        onClick={() => setShowOtherOptions(!showOtherOptions)}
        className="flex items-center gap-1.5 text-xs hover:opacity-80 transition-opacity"
        style={{ color: 'var(--text-muted)' }}
      >
        {showOtherOptions ? (
          <><ChevronUp className="w-3.5 h-3.5" /> Hide other options</>
        ) : (
          <><ChevronDown className="w-3.5 h-3.5" /> See all {otherResultsCount} other option{otherResultsCount !== 1 ? 's' : ''}</>
        )}
      </button>

      {showOtherOptions && (
        <div className="mt-2 space-y-2">
          <p className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
            Check the filename matches your song. Higher availability (green) = more reliable. Studio album versions produce the best results.
          </p>
          <ResultCategories
            groupedResults={groupedResults}
            bestResult={confidence.bestResult}
            expandedCategories={expandedCategories}
            toggleCategory={toggleCategory}
            searchTitle={searchTitle}
            onSelect={onSelect}
            noCredits={noCredits}
          />
        </div>
      )}
    </div>
  )
}

/** Render categorized result groups */
function ResultCategories({
  groupedResults,
  bestResult,
  expandedCategories,
  toggleCategory,
  searchTitle,
  onSelect,
  noCredits,
  defaultExpanded,
  alwaysShowMatchBadge,
}: {
  groupedResults: ReturnType<typeof groupResults>
  bestResult: ExtendedAudioSearchResult | null
  expandedCategories: Set<string>
  toggleCategory: (category: string) => void
  searchTitle: string
  onSelect: (index: number) => void
  noCredits: boolean
  defaultExpanded?: boolean
  alwaysShowMatchBadge?: boolean
}) {
  return (
    <div className="space-y-2 max-h-[350px] overflow-y-auto pr-1">
      {groupedResults.map(({ category, results: catResults, config }) => {
        const isExpanded = defaultExpanded || expandedCategories.has(category)
        // Filter out the best result from "other options" (when best result shown separately)
        const filteredResults = bestResult
          ? catResults.filter(r => r.index !== bestResult.index)
          : catResults
        if (filteredResults.length === 0) return null

        // Check mismatch status for each result
        const mismatchFlags = filteredResults.map(r => checkFilenameMismatch(searchTitle, r).isMismatch)
        const hasAnyMatch = mismatchFlags.some(m => !m)
        const hasAnyMismatch = mismatchFlags.some(m => m)
        // Show match/mismatch badges when there's a mix, or always in Tier 3
        const showMatchBadges = (hasAnyMatch && hasAnyMismatch) || (alwaysShowMatchBadge && hasAnyMatch)

        // Sort matching results to top when there's a mix
        const sortedResults = hasAnyMatch && hasAnyMismatch
          ? [...filteredResults].sort((a, b) => {
              const aMismatch = checkFilenameMismatch(searchTitle, a).isMismatch
              const bMismatch = checkFilenameMismatch(searchTitle, b).isMismatch
              if (aMismatch === bMismatch) return 0
              return aMismatch ? 1 : -1
            })
          : filteredResults

        const displayResults = isExpanded
          ? sortedResults
          : sortedResults.slice(0, config.maxDisplay)
        const hiddenCount = filteredResults.length - config.maxDisplay
        const hasMore = hiddenCount > 0

        return (
          <div
            key={category}
            className="border rounded-lg overflow-hidden border-[var(--card-border)] bg-[var(--secondary)]/20"
          >
            {/* Category Header */}
            <div className="px-3 py-1.5 flex items-center justify-between bg-[var(--secondary)]">
              <div className="flex items-center gap-2 text-xs font-semibold">
                <span className={config.color}>{category}</span>
                <span style={{ color: 'var(--text-muted)' }} className="font-normal">({filteredResults.length})</span>
              </div>
              {hasMore && !defaultExpanded && (
                <button
                  onClick={() => toggleCategory(category)}
                  className="text-[10px] hover:opacity-80 flex items-center gap-1"
                  style={{ color: 'var(--text-muted)' }}
                >
                  {isExpanded ? (
                    <>Show less <ChevronUp className="w-3 h-3" /></>
                  ) : (
                    <>+{hiddenCount} more <ChevronDown className="w-3 h-3" /></>
                  )}
                </button>
              )}
            </div>

            {/* Results */}
            <div className="divide-y" style={{ borderColor: 'var(--card-border)' }}>
              {displayResults.map((result) => (
                <ResultRow
                  key={result.index}
                  result={result}
                  searchTitle={searchTitle}
                  showMatchBadges={showMatchBadges}
                  onSelect={onSelect}
                  noCredits={noCredits}
                />
              ))}
            </div>
          </div>
        )
      })}
    </div>
  )
}

/** Single result row in the "other options" list */
function ResultRow({
  result,
  searchTitle,
  showMatchBadges,
  onSelect,
  noCredits,
}: {
  result: ExtendedAudioSearchResult
  searchTitle: string
  showMatchBadges: boolean
  onSelect: (index: number) => void
  noCredits: boolean
}) {
  const mismatch = useMemo(() => checkFilenameMismatch(searchTitle, result), [searchTitle, result])
  // Only show "Title match" when we actually verified a match (filename is non-empty)
  const isMatch = showMatchBadges && !mismatch.isMismatch && mismatch.filename !== ''

  return (
    <div
      className={`px-3 py-2 flex items-center gap-3 text-xs transition-colors ${isMatch ? 'bg-green-600/5' : 'hover:bg-[var(--secondary)]/50'}`}
    >
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5 flex-wrap">
          {result.is_lossless && (
            <span className="text-[8px] px-1 py-0.5 rounded bg-green-600/20 text-green-400 font-medium">
              LOSSLESS
            </span>
          )}
          {result.provider === "Spotify" && (
            <span className="text-[8px] px-1 py-0.5 rounded font-medium bg-green-600/20 text-green-400">
              Spotify
            </span>
          )}
          {result.provider === "YouTube" && (
            <span className="text-[8px] px-1 py-0.5 rounded font-medium bg-red-600/20 text-red-400">
              YouTube
            </span>
          )}
          <span className="font-medium" style={{ color: 'var(--text)' }}>
            {getDisplayName(result)} - {result.title}
          </span>
          <span className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
            ({formatQuality(result)})
          </span>
          <AvailabilityBadge seeders={result.seeders} />
          {!result.seeders && (
            <ViewCountBadge viewCount={result.view_count} />
          )}
          {isMatch ? (
            <span className="text-[8px] px-1 py-0.5 rounded font-medium bg-green-600/20 text-green-400">
              Title match
            </span>
          ) : (
            <MismatchBadge searchTitle={searchTitle} result={result} />
          )}
        </div>
        {formatMetadata(result) && (
          <div className="text-[10px] mt-0.5" style={{ color: 'var(--text-muted)' }}>
            {formatMetadata(result)}
          </div>
        )}
        {result.target_file && (
          <div className="text-[10px] mt-0.5 font-mono break-all" style={{ color: 'var(--text-muted)' }}>
            <span className="font-sans">Filename: </span>{result.target_file}
          </div>
        )}
      </div>

      <Button
        size="sm"
        onClick={() => onSelect(result.index)}
        disabled={noCredits}
        className="h-7 px-3 text-xs shrink-0 bg-[var(--brand-pink)] hover:bg-[var(--brand-pink-hover)] text-white"
      >
        <Check className="w-3 h-3 mr-1" />
        Select
      </Button>
    </div>
  )
}


/** Compute similarity between two strings using longest common subsequence ratio */
export function stringSimilarity(a: string, b: string): number {
  const al = a.toLowerCase()
  const bl = b.toLowerCase()
  if (al === bl) return 1
  // Substring match — very strong signal
  if (al.length >= 3 && bl.includes(al)) return 0.85
  if (bl.length >= 3 && al.includes(bl)) return 0.85
  // LCS-based similarity
  const m = al.length
  const n = bl.length
  if (m === 0 || n === 0) return 0
  // Use two-row DP for LCS length
  let prev = new Array(n + 1).fill(0)
  let curr = new Array(n + 1).fill(0)
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      if (al[i - 1] === bl[j - 1]) {
        curr[j] = prev[j - 1] + 1
      } else {
        curr[j] = Math.max(prev[j], curr[j - 1])
      }
    }
    ;[prev, curr] = [curr, prev]
    curr.fill(0)
  }
  const lcsLen = prev[n]
  return (2 * lcsLen) / (m + n)
}

/** Find fuzzy matches for a title among catalog tracks, sorted by relevance.
 *  When userArtist is provided, uses artist similarity as a tiebreaker
 *  (e.g. user typed "fox stevenson bruises" as artist — prefer Fox Stevenson over Lewis Capaldi). */
export function findFuzzyMatches(
  userTitle: string,
  tracks: CatalogTrackResult[],
  threshold = 0.6,
  allowExactTitle = false,
  userArtist?: string,
): CatalogTrackResult[] {
  const scored = tracks.map((track) => {
    const titleScore = stringSimilarity(userTitle, track.track_name)
    const artistBonus = userArtist ? stringSimilarity(userArtist, track.artist_name) * 0.2 : 0
    return { track, titleScore, totalScore: titleScore + artistBonus }
  })
  return scored
    .filter((s) => s.titleScore >= threshold)
    .filter((s) => allowExactTitle || s.track.track_name.toLowerCase() !== userTitle.toLowerCase())
    .sort((a, b) => b.totalScore - a.totalScore)
    .map((s) => s.track)
    // Deduplicate by artist+track name
    .filter((t, i, arr) => arr.findIndex((x) => x.artist_name === t.artist_name && x.track_name === t.track_name) === i)
}


/** Contextual guidance when search returns no results */
function NoResultsSection() {
  return (
    <div
      className="border rounded-lg p-4 space-y-3 border-[var(--card-border)] bg-[var(--secondary)]/30"
      data-testid="no-results-section"
    >
      <div className="flex items-center gap-2">
        <Info className="w-4 h-4 shrink-0" style={{ color: 'var(--text-muted)' }} />
        <span className="text-sm font-medium" style={{ color: 'var(--text)' }}>
          No audio sources found
        </span>
      </div>
      <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
        This is common for unreleased tracks, independent music, remixes/covers, or songs with unusual characters in the title.
      </p>
      <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
        Try simplifying your search — remove special characters, or search by just part of the title. Otherwise, you can upload your own audio file or paste a YouTube URL below.
      </p>
    </div>
  )
}

/** Fallback section — upload or YouTube URL */
function FallbackSection({
  tier,
  hasResults,
  fallbackMode,
  setFallbackMode,
  youtubeUrl,
  setYoutubeUrl,
  uploadFile,
  setUploadFile,
  noCredits,
  onUrlSubmit,
  onUploadSubmit,
}: {
  tier: number
  hasResults: boolean
  fallbackMode: "upload" | "url" | null
  setFallbackMode: (mode: "upload" | "url" | null) => void
  youtubeUrl: string
  setYoutubeUrl: (url: string) => void
  uploadFile: File | null
  setUploadFile: (file: File | null) => void
  noCredits: boolean
  onUrlSubmit: (e: React.FormEvent) => void
  onUploadSubmit: (e: React.FormEvent) => void
}) {
  const t = useTranslations('jobFlow')
  const isTier3WithResults = tier === 3 && hasResults
  const largeButtons = !hasResults
  const buttonHeight = largeButtons ? 'h-12' : 'h-9'
  const iconSize = largeButtons ? 'w-4 h-4' : 'w-3.5 h-3.5'
  return (
    <div className={`${isTier3WithResults ? '' : 'border-t pt-4 '}space-y-3`} style={{ borderColor: 'var(--card-border)' }}>
      <p className={isTier3WithResults ? 'text-xs font-medium' : 'text-xs'} style={{ color: 'var(--text-muted)' }}>
        {isTier3WithResults
          ? "Have your own audio? Upload it or paste a YouTube URL:"
          : hasResults
            ? 'Not finding what you need? Try simplifying your search, or provide your own audio:'
            : 'Provide your own audio:'
        }
      </p>

      {/* Two equally prominent buttons */}
      <div className="grid grid-cols-2 gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => { setFallbackMode(fallbackMode === "url" ? null : "url"); }}
          className={`${buttonHeight} text-xs ${fallbackMode === "url" ? 'border-[var(--brand-pink)] bg-[var(--brand-pink)]/10' : ''}`}
          style={{ borderColor: fallbackMode === "url" ? 'var(--brand-pink)' : 'var(--card-border)', color: 'var(--text)' }}
        >
          <Youtube className={`${iconSize} mr-1.5`} />
          {t('pasteUrlButton')}
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => { setFallbackMode(fallbackMode === "upload" ? null : "upload"); }}
          className={`${buttonHeight} text-xs ${fallbackMode === "upload" ? 'border-[var(--brand-pink)] bg-[var(--brand-pink)]/10' : ''}`}
          style={{ borderColor: fallbackMode === "upload" ? 'var(--brand-pink)' : 'var(--card-border)', color: 'var(--text)' }}
        >
          <Upload className={`${iconSize} mr-1.5`} />
          Upload file
        </Button>
      </div>

      {/* YouTube URL form */}
      {fallbackMode === "url" && (
        <form onSubmit={onUrlSubmit} className="space-y-2 p-3 rounded-lg" style={{ backgroundColor: 'var(--secondary)' }}>
          <div className="relative">
            <Youtube className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4" style={{ color: 'var(--text-muted)' }} />
            <Input
              type="url"
              placeholder="https://youtube.com/watch?v=..."
              value={youtubeUrl}
              onChange={(e) => setYoutubeUrl(e.target.value)}
              className="pl-10 text-xs"
              style={{ backgroundColor: 'var(--background)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
              autoFocus
            />
          </div>
          <p className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
            {t('urlSupportedSitesHint')}{' '}
            <a
              href="https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md"
              target="_blank"
              rel="noopener noreferrer"
              className="underline"
              style={{ color: 'var(--brand-pink)' }}
            >
              {t('urlSupportedSitesLink')}
            </a>
          </p>
          <Button
            type="submit"
            size="sm"
            disabled={!youtubeUrl.trim() || noCredits}
            className="w-full bg-[var(--brand-pink)] hover:bg-[var(--brand-pink-hover)] text-white"
          >
            {t('useThisUrl')}
          </Button>
        </form>
      )}

      {/* Upload form */}
      {fallbackMode === "upload" && (
        <form onSubmit={onUploadSubmit} className="space-y-3 p-3 rounded-lg" style={{ backgroundColor: 'var(--secondary)' }}>
          <label
            className="block border-2 border-dashed rounded-lg p-4 text-center transition-colors cursor-pointer"
            style={{
              borderColor: uploadFile ? 'rgba(255, 122, 204, 0.5)' : 'var(--card-border)',
              backgroundColor: uploadFile ? 'rgba(255, 122, 204, 0.05)' : 'transparent',
            }}
          >
            <input
              type="file"
              accept=".mp3,.wav,.flac,.m4a,.ogg,audio/*"
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file) setUploadFile(file)
              }}
              className="sr-only"
            />
            {uploadFile ? (
              <div>
                <p className="text-sm font-medium" style={{ color: 'var(--text)' }}>{uploadFile.name}</p>
                <p className="text-xs" style={{ color: 'var(--text-muted)' }}>{(uploadFile.size / 1024 / 1024).toFixed(2)} MB</p>
              </div>
            ) : (
              <div>
                <Upload className="w-6 h-6 mx-auto mb-1" style={{ color: 'var(--text-muted)' }} />
                <p className="text-xs" style={{ color: 'var(--text-muted)' }}>Click to browse or drag a file</p>
                <p className="text-[10px] mt-0.5" style={{ color: 'var(--text-muted)' }}>MP3, WAV, FLAC, M4A, or OGG</p>
              </div>
            )}
          </label>

          {/* Large file warning */}
          {uploadFile && uploadFile.size > 100 * 1024 * 1024 && (
            <div className="flex items-start gap-2 rounded p-2 text-[10px] text-amber-400 bg-amber-500/10">
              <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
              <span>Large file ({(uploadFile.size / 1024 / 1024).toFixed(0)} MB) — upload may take a while depending on your connection.</span>
            </div>
          )}

          <Button
            type="submit"
            size="sm"
            disabled={!uploadFile || noCredits}
            className="w-full bg-[var(--brand-pink)] hover:bg-[var(--brand-pink-hover)] text-white"
          >
            Use This File
          </Button>
        </form>
      )}
    </div>
  )
}

/** Match-judge notice: a quiet auto-applied tidy/correction line (with undo),
 *  or an ask-first "Did you mean?" prompt for ambiguous cases. */
function MatchNotice({
  verdict,
  appliedFrom,
  onAccept,
  onUndo,
  onDismiss,
}: {
  verdict: MatchJudgeVerdict
  appliedFrom: { artist: string; title: string } | null
  onAccept: (artist: string, title: string) => void
  onUndo: () => void
  onDismiss: () => void
}) {
  const t = useTranslations('jobFlow')
  const canonical = `${verdict.canonical_artist} — ${verdict.canonical_title}`

  // Auto-applied cosmetic tidy or confident content correction → quiet line + undo.
  if (appliedFrom && (verdict.kind === 'cosmetic' || verdict.kind === 'content')) {
    const typed = `${appliedFrom.artist} — ${appliedFrom.title}`
    return (
      <div
        className="flex items-center gap-2 text-xs px-3 py-2 rounded-lg"
        style={{ backgroundColor: 'var(--secondary)', color: 'var(--text-muted)' }}
        data-testid="match-tidy-notice"
      >
        <Check className="w-3.5 h-3.5 text-green-400 shrink-0" />
        <span className="flex-1">
          {verdict.kind === 'content'
            ? t('matchCorrectedTo', { value: canonical, typed })
            : t('matchTidiedTo', { value: canonical })}
        </span>
        <button
          type="button"
          onClick={onUndo}
          className="underline shrink-0"
          style={{ color: 'var(--brand-pink)' }}
        >
          {verdict.kind === 'content' ? t('matchUndo') : t('matchKeepMine')}
        </button>
      </div>
    )
  }

  // Ambiguous (or unconfident content) → ask before changing anything.
  if (verdict.kind === 'ambiguous' || verdict.kind === 'content') {
    const options = [
      { artist: verdict.canonical_artist, title: verdict.canonical_title },
      ...verdict.alternatives,
    ].filter((o) => o.artist && o.title)
    if (options.length === 0) return null
    return (
      <div
        className="border rounded-lg p-3 border-amber-500/30 bg-amber-500/5"
        data-testid="match-didyoumean"
      >
        <div className="flex items-center justify-between">
          <span className="flex items-center gap-2 text-sm font-medium text-amber-400">
            <Lightbulb className="w-4 h-4" />
            {t('matchDidYouMean')}
          </span>
          <button
            type="button"
            onClick={onDismiss}
            aria-label="Dismiss"
            className="opacity-60 hover:opacity-100"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="mt-2 flex flex-col gap-1.5">
          {options.map((o, i) => (
            <button
              key={`${o.artist}-${o.title}-${i}`}
              type="button"
              onClick={() => onAccept(o.artist, o.title)}
              className="text-left text-sm px-2.5 py-1.5 rounded-md hover:bg-amber-500/10 transition-colors"
              style={{ color: 'var(--text)' }}
            >
              {o.artist} — {o.title}
            </button>
          ))}
        </div>
      </div>
    )
  }

  return null
}
