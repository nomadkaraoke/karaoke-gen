"use client"

import { useState, useEffect, useMemo } from "react"
import { useTranslations } from 'next-intl'
import { api, ApiError } from "@/lib/api"
import {
  ExtendedAudioSearchResult,
  groupResults,
  getDisplayName,
  formatCount,
  formatMetadata,
  formatQuality,
  getAvailabilityLabel,
  checkFilenameMismatch,
} from "@/lib/audio-search-utils"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog"
import { Loader2, Music2, ChevronDown, ChevronUp, Lightbulb, Search, Youtube, Upload } from "lucide-react"
import { ResultCostChip } from "./ResultCostChip"

// Version from pyproject.toml (single source of truth)
const APP_VERSION = process.env.NEXT_PUBLIC_APP_VERSION || "0.0.0"

interface AudioSearchDialogProps {
  jobId: string
  open: boolean
  onClose: () => void
  onSelect: () => void
  searchArtist?: string
  searchTitle?: string
}

export function AudioSearchDialog({ jobId, open, onClose, onSelect, searchArtist, searchTitle }: AudioSearchDialogProps) {
  const t = useTranslations('audioSearch')
  const [results, setResults] = useState<ExtendedAudioSearchResult[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [isSelecting, setIsSelecting] = useState<number | null>(null)
  const [error, setError] = useState("")
  const [expandedCategories, setExpandedCategories] = useState<Set<string>>(new Set())
  const [showGuidance, setShowGuidance] = useState(false)

  // Editable search terms + recovery actions (edit → re-search, or provide own audio).
  const [editArtist, setEditArtist] = useState(searchArtist || "")
  const [editTitle, setEditTitle] = useState(searchTitle || "")
  const [isResearching, setIsResearching] = useState(false)
  const [actionError, setActionError] = useState("")
  // Fallback (own audio) — auto-opens when a search returns nothing.
  const [fallbackMode, setFallbackMode] = useState<"url" | "upload" | null>(null)
  const [youtubeUrl, setYoutubeUrl] = useState("")
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [isSubmittingSource, setIsSubmittingSource] = useState(false)

  // The title used for filename-mismatch badges tracks the (possibly edited) terms.
  const effectiveTitle = editTitle || searchTitle

  // Load results when dialog opens
  useEffect(() => {
    if (open) {
      loadResults()
      setExpandedCategories(new Set()) // Reset expanded state
      setShowGuidance(false)
      setFallbackMode(null)
      setActionError("")
      setYoutubeUrl("")
      setUploadFile(null)
      // Seed editable terms from props; refined again from the API response below.
      setEditArtist(searchArtist || "")
      setEditTitle(searchTitle || "")
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, jobId])

  async function loadResults() {
    setIsLoading(true)
    setError("")
    try {
      const data = await api.getAudioSearchResults(jobId)
      setResults((data.results || []) as ExtendedAudioSearchResult[])
      // Prefer the job's stored search terms so the edit fields reflect reality.
      if (data.artist) setEditArtist(data.artist)
      if (data.title) setEditTitle(data.title)
      if ((data.results || []).length === 0) setFallbackMode(null)
    } catch (err: any) {
      // A parked job with no cached results returns 400 — that's the dead-end we
      // are fixing, so render it as the empty state (with the refine bar), not a
      // red banner. Any other failure (network / 5xx / session) is a real error.
      if (err instanceof ApiError && err.status === 400) {
        setResults([])
      } else {
        setResults([])
        setError(err?.message || t('selectFailed'))
      }
    } finally {
      setIsLoading(false)
    }
  }

  // Group results by category
  const groupedResults = useMemo(() => groupResults(results), [results])
  const totalCategories = groupedResults.length

  async function handleSelect(index: number) {
    setIsSelecting(index)
    setError("")
    try {
      await api.selectAudioResult(jobId, index)
      onSelect()
      onClose()
    } catch (err: any) {
      setError(err.message || t('selectFailed'))
    } finally {
      setIsSelecting(null)
    }
  }

  async function handleResearch() {
    if (!editArtist.trim() || !editTitle.trim() || isResearching) return
    setIsResearching(true)
    setActionError("")
    setError("")
    try {
      const data = await api.researchAudio(jobId, { artist: editArtist.trim(), title: editTitle.trim() })
      const fresh = (data.results || []) as ExtendedAudioSearchResult[]
      setResults(fresh)
      setExpandedCategories(new Set())
      // Nothing found again → auto-open the own-audio fallback; results → close it.
      setFallbackMode(prev => (fresh.length === 0 ? (prev ?? "url") : null))
    } catch (err: any) {
      setActionError(err.message || t('researchFailed'))
    } finally {
      setIsResearching(false)
    }
  }

  async function handleProvideUrl() {
    const url = youtubeUrl.trim()
    if (!url || isSubmittingSource) return
    setIsSubmittingSource(true)
    setActionError("")
    try {
      await api.provideUrlForJob(jobId, url)
      onSelect()
      onClose()
    } catch (err: any) {
      setActionError(err.message || t('urlFailed'))
    } finally {
      setIsSubmittingSource(false)
    }
  }

  async function handleUpload() {
    if (!uploadFile || isSubmittingSource) return
    setIsSubmittingSource(true)
    setActionError("")
    try {
      await api.attachUploadToJob(jobId, uploadFile)
      onSelect()
      onClose()
    } catch (err: any) {
      setActionError(err.message || t('uploadFailed'))
    } finally {
      setIsSubmittingSource(false)
    }
  }

  function toggleCategory(category: string) {
    setExpandedCategories(prev => {
      const next = new Set(prev)
      if (next.has(category)) next.delete(category)
      else next.add(category)
      return next
    })
  }

  const hasResults = results.length > 0
  const busy = isResearching || isSubmittingSource

  return (
    <Dialog open={open} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-7xl w-[95vw] max-h-[90vh] flex flex-col bg-card border-border !p-0 gap-0 overflow-hidden">
        <div className="px-4 py-2.5 border-b border-border shrink-0 bg-card flex items-center justify-between">
          <DialogTitle className="text-foreground text-sm font-semibold flex items-center gap-2">
            {t('selectAudioSource')}
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-secondary text-muted-foreground font-mono">v{APP_VERSION}</span>
            <span className="text-muted-foreground font-normal text-xs">
              ({t('resultsCount', { count: results.length, categories: totalCategories })})
            </span>
          </DialogTitle>
        </div>

        {/* Refine bar: edit the search terms and search again. Always available so a
            wrong-track match or a no-results dead-end is never a trap. */}
        <div className="px-4 py-2 border-b border-border bg-secondary/30 shrink-0 space-y-2">
          <div className="flex flex-col sm:flex-row sm:items-end gap-2">
            <div className="flex-1 min-w-0">
              <label className="block text-[10px] text-muted-foreground mb-0.5">{t('artistLabel')}</label>
              <Input
                value={editArtist}
                onChange={(e) => setEditArtist(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') handleResearch() }}
                placeholder={t('artistLabel')}
                disabled={busy}
                className="h-8 text-xs bg-background"
              />
            </div>
            <div className="flex-1 min-w-0">
              <label className="block text-[10px] text-muted-foreground mb-0.5">{t('titleLabel')}</label>
              <Input
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') handleResearch() }}
                placeholder={t('titleLabel')}
                disabled={busy}
                className="h-8 text-xs bg-background"
              />
            </div>
            <Button
              size="sm"
              onClick={handleResearch}
              disabled={busy || !editArtist.trim() || !editTitle.trim()}
              className="h-8 text-xs bg-[var(--brand-pink)] hover:bg-[var(--brand-pink-hover)] text-white shrink-0"
            >
              {isResearching ? <Loader2 className="w-3.5 h-3.5 animate-spin mr-1" /> : <Search className="w-3.5 h-3.5 mr-1" />}
              {t('searchAgain')}
            </Button>
          </div>

          {/* Provide-your-own-audio fallback */}
          <div className="flex items-center gap-3 text-[10px]">
            <button
              onClick={() => setFallbackMode(fallbackMode === "url" ? null : "url")}
              className={`flex items-center gap-1 hover:text-foreground transition-colors ${fallbackMode === "url" ? 'text-[var(--brand-pink)]' : 'text-muted-foreground'}`}
            >
              <Youtube className="w-3 h-3" /> {t('pasteUrl')}
            </button>
            <button
              onClick={() => setFallbackMode(fallbackMode === "upload" ? null : "upload")}
              className={`flex items-center gap-1 hover:text-foreground transition-colors ${fallbackMode === "upload" ? 'text-[var(--brand-pink)]' : 'text-muted-foreground'}`}
            >
              <Upload className="w-3 h-3" /> {t('uploadFileAction')}
            </button>
          </div>

          {fallbackMode === "url" && (
            <div className="flex flex-col sm:flex-row gap-2">
              <Input
                type="url"
                value={youtubeUrl}
                onChange={(e) => setYoutubeUrl(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') handleProvideUrl() }}
                placeholder={t('urlPlaceholder')}
                disabled={busy}
                className="h-8 text-xs bg-background flex-1"
              />
              <Button
                size="sm"
                onClick={handleProvideUrl}
                disabled={busy || !youtubeUrl.trim()}
                className="h-8 text-xs bg-[var(--brand-pink)] hover:bg-[var(--brand-pink-hover)] text-white shrink-0"
              >
                {isSubmittingSource ? <Loader2 className="w-3.5 h-3.5 animate-spin mr-1" /> : null}
                {t('useThisUrl')}
              </Button>
            </div>
          )}

          {fallbackMode === "upload" && (
            <div className="flex flex-col sm:flex-row gap-2 items-stretch sm:items-center">
              <label className="flex-1 border border-dashed border-border rounded px-2 py-1.5 text-[10px] text-muted-foreground cursor-pointer hover:border-[var(--brand-pink)] truncate">
                <input
                  type="file"
                  accept=".mp3,.wav,.flac,.m4a,.ogg,audio/*"
                  onChange={(e) => setUploadFile(e.target.files?.[0] || null)}
                  className="sr-only"
                  disabled={busy}
                />
                {uploadFile ? uploadFile.name : t('chooseFile')}
              </label>
              <Button
                size="sm"
                onClick={handleUpload}
                disabled={busy || !uploadFile}
                className="h-8 text-xs bg-[var(--brand-pink)] hover:bg-[var(--brand-pink-hover)] text-white shrink-0"
              >
                {isSubmittingSource ? <Loader2 className="w-3.5 h-3.5 animate-spin mr-1" /> : null}
                {t('useThisFile')}
              </Button>
            </div>
          )}

          {actionError && (
            <p className="text-[10px] text-red-400">{actionError}</p>
          )}
        </div>

        {/* Guidance header (collapsed by default) — only meaningful when there are results */}
        {!isLoading && hasResults && (
          <div className="px-4 py-1.5 border-b border-border bg-secondary/30">
            <button
              onClick={() => setShowGuidance(!showGuidance)}
              className="flex items-center gap-1.5 text-[10px] text-muted-foreground hover:text-foreground transition-colors"
            >
              <Lightbulb className="w-3 h-3" />
              {t('tipsForChoosing')}
              {showGuidance ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
            </button>
            {showGuidance && (
              <ul className="text-[10px] text-muted-foreground mt-1.5 ml-5 space-y-0.5 pb-1">
                <li>{t('tipFilename')}</li>
                <li>{t('tipAvailability')}</li>
                <li>{t('tipStudioAlbum')}</li>
                <li>{t('tipVinylRips')}</li>
                <li>{t('tipYoutube')}</li>
              </ul>
            )}
          </div>
        )}

        {error && (
          <div className="px-4 py-2 text-xs text-red-400 bg-red-500/10 border-b border-border">
            {error}
          </div>
        )}

        {isLoading ? (
          <div className="flex-1 flex items-center justify-center py-12">
            <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
            <span className="ml-2 text-sm text-muted-foreground">{t('loading')}</span>
          </div>
        ) : !hasResults ? (
          <div className="flex-1 flex flex-col items-center justify-center py-12 text-muted-foreground px-6 text-center">
            <Music2 className="w-8 h-8 mb-2 opacity-50" />
            <p className="text-sm font-medium text-foreground">{t('noAudioSources')}</p>
            <p className="text-xs mt-1.5 max-w-md">{t('noResultsHelp')}</p>
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto min-h-0 p-4">
            {/* Two-column grid on desktop */}
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
              {groupedResults.map(({ category, results: catResults, config }) => {
                const isExpanded = expandedCategories.has(category)
                const displayResults = isExpanded ? catResults : catResults.slice(0, config.maxDisplay)
                const hiddenCount = catResults.length - config.maxDisplay
                const hasMore = hiddenCount > 0

                return (
                  <div key={category} className="border border-border rounded-lg overflow-hidden bg-secondary/20">
                    {/* Category Header */}
                    <div className={`px-3 py-2 bg-secondary flex items-center justify-between ${config.color}`}>
                      <div className="flex items-center gap-2 text-xs font-semibold">
                        <span>{category}</span>
                        <span className="text-muted-foreground font-normal">({catResults.length})</span>
                      </div>
                      {hasMore && (
                        <button
                          onClick={() => toggleCategory(category)}
                          className="text-[10px] text-muted-foreground hover:text-foreground flex items-center gap-1"
                        >
                          {isExpanded ? (
                            <>Show less <ChevronUp className="w-3 h-3" /></>
                          ) : (
                            <>{t('moreVersions', { count: hiddenCount })} <ChevronDown className="w-3 h-3" /></>
                          )}
                        </button>
                      )}
                    </div>

                    {/* Results in category */}
                    <div className="divide-y divide-slate-700/50">
                      {displayResults.map((result) => {
                        const mismatch = effectiveTitle ? checkFilenameMismatch(effectiveTitle, result) : null
                        const hasMismatch = mismatch?.isMismatch ?? false

                        return (
                          <div
                            key={result.index}
                            className={`px-3 py-1.5 hover:bg-secondary/50 flex items-center gap-2 text-xs ${
                              hasMismatch ? 'opacity-60' : ''
                            }`}
                          >
                            {/* Index */}
                            <span className="w-5 text-muted-foreground font-mono shrink-0 text-[10px]">
                              {result.index + 1}.
                            </span>

                            {/* Main content - 2 lines */}
                            <div className="flex-1 min-w-0">
                              {/* Line 1: badges + artist + title + quality + size + availability */}
                              <div className="flex items-center gap-1 flex-wrap">
                                {result.is_lossless && (
                                  <span className="text-[8px] px-1 py-0.5 rounded bg-green-600/20 text-green-400 font-medium">
                                    {t('lossless')}
                                  </span>
                                )}
                                {result.quality_data?.media?.toLowerCase() === 'vinyl' && (
                                  <span className="text-[8px] px-1 py-0.5 rounded bg-red-600/20 text-red-400 font-medium">
                                    {t('vinyl')}
                                  </span>
                                )}
                                {result.provider === "YouTube" && (
                                  <span className="text-[8px] px-1 py-0.5 rounded font-medium bg-red-600/20 text-red-400">
                                    YouTube
                                  </span>
                                )}
                                {hasMismatch && (
                                  <span
                                    title={`Expected "${effectiveTitle}" but filename is "${mismatch!.filename}"${mismatch!.suggestedTrack ? ` (looks like "${mismatch!.suggestedTrack}")` : ''}`}
                                    className="text-[8px] px-1 py-0.5 rounded font-medium bg-yellow-600/20 text-yellow-400 cursor-help"
                                  >
                                    {t('wrongTrack')}
                                  </span>
                                )}
                                <span className="text-green-400 font-medium">{getDisplayName(result)}</span>
                                <span className="text-muted-foreground">-</span>
                                <span className="text-foreground">{result.title}</span>
                                <span className={`text-[10px] ${result.is_lossless ? "text-green-400" : "text-muted-foreground"}`}>
                                  ({formatQuality(result)})
                                </span>
                                <span className="text-[10px] text-muted-foreground">{result.formatted_size || '-'}</span>
                                {result.seeders !== undefined && result.seeders !== null ? (
                                  (() => {
                                    const { text, tooltip } = getAvailabilityLabel(result.seeders)
                                    return (
                                      <span
                                        title={tooltip}
                                        className={`text-[8px] px-1 py-0.5 rounded font-medium cursor-help ${
                                          result.seeders >= 50 ? 'bg-green-600/20 text-green-400' :
                                          result.seeders >= 10 ? 'bg-yellow-600/20 text-yellow-400' :
                                          'bg-red-600/20 text-red-400'
                                        }`}
                                      >
                                        {text} {t('availability')}
                                      </span>
                                    )
                                  })()
                                ) : result.view_count !== undefined ? (
                                  <span className={`text-[8px] px-1 py-0.5 rounded font-medium ${
                                    result.view_count >= 1000000 ? 'bg-green-600/20 text-green-400' :
                                    result.view_count >= 10000 ? 'bg-yellow-600/20 text-yellow-400' :
                                    'bg-muted text-muted-foreground'
                                  }`}>
                                    {t('views', { count: formatCount(result.view_count) })}
                                  </span>
                                ) : null}
                                <ResultCostChip durationSeconds={result.duration} />
                              </div>

                              {/* Line 2: metadata + filename */}
                              {(formatMetadata(result) || result.target_file) && (
                                <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
                                  {formatMetadata(result) && (
                                    <span className="truncate">{formatMetadata(result)}</span>
                                  )}
                                  {result.target_file && (
                                    <span className="font-mono truncate">&quot;{result.target_file}&quot;</span>
                                  )}
                                </div>
                              )}
                            </div>

                            {/* Select button */}
                            <Button
                              size="sm"
                              onClick={() => handleSelect(result.index)}
                              disabled={isSelecting !== null}
                              className="w-14 h-6 text-[9px] bg-amber-600 hover:bg-amber-500 text-foreground px-2 shrink-0"
                            >
                              {isSelecting === result.index ? (
                                <Loader2 className="w-3 h-3 animate-spin" />
                              ) : (
                                t('select')
                              )}
                            </Button>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
