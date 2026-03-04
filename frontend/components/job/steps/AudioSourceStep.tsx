"use client"

import { useState, useEffect, useMemo, useCallback, useRef } from "react"
import { api, ApiError, type UploadProgress } from "@/lib/api"
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
import { Loader2, Music2, ChevronDown, ChevronUp, Upload, Youtube, ArrowLeft, Check, AlertTriangle, CheckCircle2, Lightbulb, Info } from "lucide-react"
import { BuyCreditsDialog } from "@/components/credits/BuyCreditsDialog"

interface AudioSourceStepProps {
  artist: string
  title: string
  displayArtist: string
  displayTitle: string
  isPrivate: boolean
  onSearchCompleted: (searchSessionId: string) => void
  onJobCreated: (jobId: string, source: "upload" | "url") => void
  onSearchResultChosen: (resultIndex: number) => void
  onFallbackComplete: () => void
  onBack: () => void
  noCredits: boolean
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
  displayArtist,
  displayTitle,
  isPrivate,
  onSearchCompleted,
  onJobCreated,
  onSearchResultChosen,
  onFallbackComplete,
  onBack,
  noCredits,
}: AudioSourceStepProps) {
  const [results, setResults] = useState<ExtendedAudioSearchResult[]>([])
  const [isSearching, setIsSearching] = useState(true)
  const [error, setError] = useState("")
  const [isCreditError, setIsCreditError] = useState(false)
  const [showOtherOptions, setShowOtherOptions] = useState(false)
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set())
  const searchTriggered = useRef(false)

  const [showBuyCreditsDialog, setShowBuyCreditsDialog] = useState(false)

  // Fallback form state
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [youtubeUrl, setYoutubeUrl] = useState("")
  const [isSubmittingFallback, setIsSubmittingFallback] = useState(false)
  const [fallbackMode, setFallbackMode] = useState<"upload" | "url" | null>(null)
  const [uploadProgress, setUploadProgress] = useState<UploadProgress | null>(null)

  // Search with retry for network failures
  const doSearch = useCallback(async (retryCount = 0): Promise<void> => {
    setIsSearching(true)
    setError("")
    setIsCreditError(false)
    try {
      const response = await api.searchStandalone(artist, title)
      // Notify parent of completed session (no job created yet)
      onSearchCompleted(response.search_session_id)
      setResults(response.results as ExtendedAudioSearchResult[])
    } catch (err) {
      if (err instanceof ApiError && err.status === 402) {
        setIsCreditError(true)
        setError("You're out of credits. Buy more to continue creating karaoke videos.")
      } else if (err instanceof ApiError) {
        setError(err.message)
      } else if (retryCount < 1) {
        // Network errors (CORS, timeout, connection reset) - auto-retry once
        return doSearch(retryCount + 1)
      } else {
        setError("Search failed due to a network error. Please try again.")
      }
    } finally {
      setIsSearching(false)
    }
  }, [artist, title, onSearchCompleted])

  // Auto-trigger search on mount
  useEffect(() => {
    if (searchTriggered.current) return
    searchTriggered.current = true
    doSearch()
  }, [doSearch])

  const confidence = useMemo(() => getSearchConfidence(results, title), [results, title])
  const groupedResults = useMemo(() => groupResults(results), [results])
  const otherResultsCount = results.length > 0 && confidence.bestResult ? results.length - 1 : 0

  function handleSelect(index: number) {
    // Don't call the API here — defer selectAudioResult to the Customize & Create step
    // so the job stays in awaiting_audio_selection (hidden from Recent Jobs) until confirmed
    onSearchResultChosen(index)
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

  async function handleUploadSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!uploadFile) return

    setIsSubmittingFallback(true)
    setError("")
    setIsCreditError(false)
    setIsTimeoutError(false)
    setUploadProgress(null)
    try {
      const response = await api.uploadJobSmart(
        uploadFile, artist.trim(), title.trim(),
        { is_private: isPrivate },
        (progress) => setUploadProgress(progress),
      )
      onJobCreated(response.job_id, "upload" as const)
      onFallbackComplete()
    } catch (err) {
      if (err instanceof ApiError && err.status === 402) {
        setIsCreditError(true)
        setError("You're out of credits. Buy more to continue creating karaoke videos.")
      } else if (err instanceof ApiError) {
        setError(err.message)
      } else {
        setError("Failed to upload file")
      }
    } finally {
      setIsSubmittingFallback(false)
      setUploadProgress(null)
    }
  }

  async function handleUrlSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!youtubeUrl.trim()) return

    setIsSubmittingFallback(true)
    setError("")
    setIsCreditError(false)
    try {
      const response = await api.createJobFromUrl(youtubeUrl.trim(), artist.trim(), title.trim(), {
        is_private: isPrivate,
      })
      onJobCreated(response.job_id, "url" as const)
      onFallbackComplete()
    } catch (err) {
      if (err instanceof ApiError && err.status === 402) {
        setIsCreditError(true)
        setError("You're out of credits. Buy more to continue creating karaoke videos.")
      } else if (err instanceof ApiError) {
        setError(err.message)
      } else {
        setError("Failed to create job from URL")
      }
    } finally {
      setIsSubmittingFallback(false)
    }
  }

  const isLossyBest = confidence.bestCategory === 'YOUTUBE'

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
          disabled={isSubmittingFallback}
          style={{ color: 'var(--text-muted)' }}
        >
          <ArrowLeft className="w-4 h-4 mr-1" />
          Back
        </Button>
      </div>

      {/* Error display */}
      {error && (
        <div className="text-sm rounded p-3 text-red-400 bg-red-500/10">
          <p>{error}</p>
          {isCreditError && (
            <button onClick={() => setShowBuyCreditsDialog(true)} className="inline-block mt-1 font-medium underline" style={{ color: 'var(--brand-pink)' }}>Buy Credits</button>
          )}
          {!isCreditError && (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-1">
              <button onClick={() => { searchTriggered.current = false; doSearch() }} className="font-medium underline" style={{ color: 'var(--brand-pink)' }}>Retry</button>
              <span className="text-xs text-muted-foreground">
                Still not working? <a href="mailto:andrew@nomadkaraoke.com" className="underline hover:text-foreground">Email andrew@nomadkaraoke.com</a>
              </span>
            </div>
          )}
        </div>
      )}

      {/* Loading state */}
      {isSearching && (
        <div className="flex flex-col items-center justify-center py-12 gap-3">
          <Loader2 className="w-8 h-8 animate-spin" style={{ color: 'var(--brand-pink)' }} />
          <div className="text-center">
            <p className="text-sm font-medium" style={{ color: 'var(--text)' }}>Searching for audio sources...</p>
            <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
              This usually takes 15-30 seconds
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
            isSubmittingFallback={isSubmittingFallback}
            noCredits={noCredits}
            onUrlSubmit={handleUrlSubmit}
            onUploadSubmit={handleUploadSubmit}
            uploadProgress={uploadProgress}
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
      {!isSearching && results.length === 0 && !error && (
        <NoResultsSection />
      )}

      {/* Fallback section for Tier 1/2 and no-results — at the bottom */}
      {!isSearching && (confidence.tier !== 3 || results.length === 0) && (
        <FallbackSection
          tier={confidence.tier}
          hasResults={results.length > 0}
          fallbackMode={fallbackMode}
          setFallbackMode={setFallbackMode}
          youtubeUrl={youtubeUrl}
          setYoutubeUrl={setYoutubeUrl}
          uploadFile={uploadFile}
          setUploadFile={setUploadFile}
          isSubmittingFallback={isSubmittingFallback}
          noCredits={noCredits}
          onUrlSubmit={handleUrlSubmit}
          onUploadSubmit={handleUploadSubmit}
          uploadProgress={uploadProgress}
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
          {isPerfect ? 'Perfect match found' : 'Recommended'}
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
            <div className="flex items-baseline gap-1.5">
              <span className="text-[10px] shrink-0" style={{ color: 'var(--text-muted)' }}>Release:</span>
              <span className="font-medium text-sm" style={{ color: 'var(--text)' }}>
                {getDisplayName(bestResult)} - {bestResult.title}
              </span>
            </div>
            {bestResult.target_file && (
              <div className="flex items-baseline gap-1.5 mt-0.5">
                <span className="text-[10px] shrink-0" style={{ color: 'var(--text-muted)' }}>Filename:</span>
                <span className="font-medium text-xs font-mono break-all" style={{ color: 'var(--text)' }}>
                  {bestResult.target_file}
                </span>
              </div>
            )}
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
  isSubmittingFallback,
  noCredits,
  onUrlSubmit,
  onUploadSubmit,
  uploadProgress,
}: {
  tier: number
  hasResults: boolean
  fallbackMode: "upload" | "url" | null
  setFallbackMode: (mode: "upload" | "url" | null) => void
  youtubeUrl: string
  setYoutubeUrl: (url: string) => void
  uploadFile: File | null
  setUploadFile: (file: File | null) => void
  isSubmittingFallback: boolean
  noCredits: boolean
  onUrlSubmit: (e: React.FormEvent) => void
  onUploadSubmit: (e: React.FormEvent) => void
  uploadProgress: UploadProgress | null
}) {
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
          YouTube URL
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
              disabled={isSubmittingFallback}
              autoFocus
            />
          </div>
          <Button
            type="submit"
            size="sm"
            disabled={!youtubeUrl.trim() || isSubmittingFallback || noCredits}
            className="w-full bg-[var(--brand-pink)] hover:bg-[var(--brand-pink-hover)] text-white"
          >
            {isSubmittingFallback ? (
              <><Loader2 className="w-3 h-3 mr-1 animate-spin" /> Creating...</>
            ) : (
              "Use This URL"
            )}
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
              disabled={isSubmittingFallback}
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
          {uploadFile && uploadFile.size > 100 * 1024 * 1024 && !isSubmittingFallback && (
            <div className="flex items-start gap-2 rounded p-2 text-[10px] text-amber-400 bg-amber-500/10">
              <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
              <span>Large file ({(uploadFile.size / 1024 / 1024).toFixed(0)} MB) — upload may take a while depending on your connection.</span>
            </div>
          )}

          {/* Upload progress bar */}
          {uploadProgress && (
            <div className="space-y-1">
              <div className="flex items-center justify-between text-[10px]" style={{ color: 'var(--text-muted)' }}>
                <span>
                  {uploadProgress.phase === 'creating' && 'Preparing upload...'}
                  {uploadProgress.phase === 'uploading' && `Uploading... ${(uploadProgress.loaded / 1024 / 1024).toFixed(1)} / ${(uploadProgress.total / 1024 / 1024).toFixed(1)} MB`}
                  {uploadProgress.phase === 'finalizing' && 'Finalizing...'}
                </span>
                {uploadProgress.phase === 'uploading' && uploadProgress.total > 0 && (
                  <span>{Math.round((uploadProgress.loaded / uploadProgress.total) * 100)}%</span>
                )}
              </div>
              <div className="h-1.5 rounded-full overflow-hidden" style={{ backgroundColor: 'var(--card-border)' }}>
                <div
                  className="h-full rounded-full transition-all duration-300"
                  style={{
                    backgroundColor: 'var(--brand-pink)',
                    width: uploadProgress.phase === 'creating' ? '5%'
                      : uploadProgress.phase === 'finalizing' ? '95%'
                      : uploadProgress.total > 0 ? `${Math.round((uploadProgress.loaded / uploadProgress.total) * 90) + 5}%`
                      : '5%',
                  }}
                />
              </div>
            </div>
          )}

          <Button
            type="submit"
            size="sm"
            disabled={!uploadFile || isSubmittingFallback || noCredits}
            className="w-full bg-[var(--brand-pink)] hover:bg-[var(--brand-pink-hover)] text-white"
          >
            {isSubmittingFallback ? (
              <><Loader2 className="w-3 h-3 mr-1 animate-spin" /> Uploading...</>
            ) : (
              "Upload & Create"
            )}
          </Button>
        </form>
      )}
    </div>
  )
}
