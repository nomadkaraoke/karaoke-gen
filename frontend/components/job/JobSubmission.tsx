"use client"

import { useState, useEffect, useMemo, useCallback, useRef } from "react"
import { useTranslations } from 'next-intl'
import { api, ApiError } from "@/lib/api"
import type { CatalogArtistResult, CatalogTrackResult, CommunityCheckResponse } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { useTenant } from "@/lib/tenant"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { AutocompleteInput, type AutocompleteSuggestion } from "@/components/ui/autocomplete-input"
import { CommunityVersionBanner } from "@/components/job/CommunityVersionBanner"
import { Label } from "@/components/ui/label"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Upload, Youtube, Music, Loader2, ChevronDown, ChevronRight, AlertTriangle } from "lucide-react"
import { BuyCreditsDialog } from "@/components/credits/BuyCreditsDialog"
import { useToast } from "@/hooks/use-toast"

interface JobSubmissionProps {
  onJobCreated: () => void
}

export function JobSubmission({ onJobCreated }: JobSubmissionProps) {
  const t = useTranslations('jobSubmission')
  const tFlow = useTranslations('jobFlow')
  const { user, fetchUser } = useAuth()
  const { features, isDefault, tenant } = useTenant()
  const { toast } = useToast()
  const isAdmin = user?.role === "admin"

  // Determine which tabs are available based on tenant features
  const availableTabs = useMemo(() => {
    const tabs: string[] = []
    if (features.audio_search) tabs.push("search")
    if (features.file_upload) tabs.push("upload")
    if (features.youtube_url) tabs.push("url")
    // Fallback: at least show upload if no tabs enabled
    if (tabs.length === 0) tabs.push("upload")
    return tabs
  }, [features])

  // Default to first available tab
  const [activeTab, setActiveTab] = useState(availableTabs[0])

  // Keep activeTab in sync with availableTabs when features change
  useEffect(() => {
    if (!availableTabs.includes(activeTab)) {
      setActiveTab(availableTabs[0])
    }
  }, [availableTabs, activeTab])

  const [error, setError] = useState("")
  const [isCreditError, setIsCreditError] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)

  // Upload form
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [uploadArtist, setUploadArtist] = useState("")
  const [uploadTitle, setUploadTitle] = useState("")

  // URL form
  const [youtubeUrl, setYoutubeUrl] = useState("")
  const [youtubeArtist, setYoutubeArtist] = useState("")
  const [youtubeTitle, setYoutubeTitle] = useState("")

  // Audio search form
  const [searchArtist, setSearchArtist] = useState("")
  const [searchTitle, setSearchTitle] = useState("")
  // Display As overrides (optional - if empty, search values used for display)
  const [displayArtist, setDisplayArtist] = useState("")
  const [displayTitle, setDisplayTitle] = useState("")
  const [showDisplayAs, setShowDisplayAs] = useState(false)

  // Private (no YouTube upload) mode - Dropbox only, no YouTube/GDrive
  const [isPrivate, setIsPrivate] = useState(false)
  const [showBuyCreditsDialog, setShowBuyCreditsDialog] = useState(false)

  // Community version check (Search tab only)
  const [communityData, setCommunityData] = useState<CommunityCheckResponse | null>(null)
  const [communityDismissed, setCommunityDismissed] = useState(false)
  const communityCheckRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Credit enforcement
  const noCredits = !isAdmin && user?.credits === 0

  async function showCreditDeductedToast() {
    await fetchUser()
    const remaining = useAuth.getState().user?.credits ?? 0
    toast({
      title: "Credit used",
      description: `1 credit deducted. ${remaining} remaining.`,
    })
  }

  // --- Autocomplete helpers ---

  const fetchArtistSuggestions = useCallback(async (query: string): Promise<AutocompleteSuggestion[]> => {
    try {
      const results = await api.searchCatalogArtists(query)
      return results.map((artist: CatalogArtistResult) => ({
        key: artist.mbid || artist.spotify_id || artist.name,
        label: artist.name,
        description: artist.disambiguation || undefined,
        meta: artist.popularity != null ? `${artist.popularity}` : undefined,
        data: artist,
      }))
    } catch {
      return []
    }
  }, [])

  const fetchTrackSuggestions = useCallback(
    (artistFilter?: string) =>
      async (query: string): Promise<AutocompleteSuggestion[]> => {
        try {
          const results = await api.searchCatalogTracks(query, artistFilter || undefined)
          return results.map((track: CatalogTrackResult) => ({
            key: track.track_id || `${track.artist_name}-${track.track_name}`,
            label: track.track_name,
            description: track.artist_name,
            meta: track.duration_ms
              ? `${Math.floor(track.duration_ms / 60000)}:${String(Math.floor((track.duration_ms % 60000) / 1000)).padStart(2, "0")}`
              : undefined,
            data: track,
          }))
        } catch {
          return []
        }
      },
    []
  )

  // Community check: fires when both searchArtist and searchTitle have values (Search tab only)
  useEffect(() => {
    // Reset when inputs change
    setCommunityData(null)
    setCommunityDismissed(false)

    if (communityCheckRef.current) {
      clearTimeout(communityCheckRef.current)
    }

    const artist = searchArtist.trim()
    const title = searchTitle.trim()
    if (!artist || !title || artist.length < 2 || title.length < 2) {
      return
    }

    communityCheckRef.current = setTimeout(async () => {
      try {
        const result = await api.checkCommunityVersions(artist, title)
        setCommunityData(result)
      } catch {
        // Silently fail - community check is non-critical
      }
    }, 500)

    return () => {
      if (communityCheckRef.current) {
        clearTimeout(communityCheckRef.current)
      }
    }
  }, [searchArtist, searchTitle])

  async function handleUploadSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError("")
    setIsCreditError(false)

    if (!uploadFile) {
      setError("Please select an audio file")
      return
    }
    if (!uploadArtist.trim() || !uploadTitle.trim()) {
      setError("Please enter both artist and title")
      return
    }

    setIsSubmitting(true)
    try {
      await api.uploadJob(uploadFile, uploadArtist.trim(), uploadTitle.trim(), {
        is_private: isPrivate,
      })
      setUploadFile(null)
      setUploadArtist("")
      setUploadTitle("")
      onJobCreated()
      showCreditDeductedToast()
    } catch (err) {
      if (err instanceof ApiError && err.status === 402) {
        setIsCreditError(true)
        setError(t('outOfCredits'))
      } else if (err instanceof ApiError) {
        setError(err.message)
      } else {
        setError(t('failedToCreateJob'))
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  async function handleUrlSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError("")
    setIsCreditError(false)

    if (!youtubeUrl.trim()) {
      setError("Please enter a URL")
      return
    }
    if (!youtubeArtist.trim() || !youtubeTitle.trim()) {
      setError("Please enter both artist and title")
      return
    }

    setIsSubmitting(true)
    try {
      await api.createJobFromUrl(
        youtubeUrl.trim(),
        youtubeArtist.trim(),
        youtubeTitle.trim(),
        {
          is_private: isPrivate,
        }
      )
      setYoutubeUrl("")
      setYoutubeArtist("")
      setYoutubeTitle("")
      onJobCreated()
      showCreditDeductedToast()
    } catch (err) {
      if (err instanceof ApiError && err.status === 402) {
        setIsCreditError(true)
        setError(t('outOfCredits'))
      } else if (err instanceof ApiError) {
        setError(err.message)
      } else {
        setError(t('failedToCreateJob'))
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  async function handleSearchSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError("")
    setIsCreditError(false)

    if (!searchArtist.trim() || !searchTitle.trim()) {
      setError("Please enter both artist and title")
      return
    }

    setIsSubmitting(true)
    try {
      await api.searchAudio(searchArtist.trim(), searchTitle.trim(), false, {
        is_private: isPrivate,
        // Display overrides (empty string means use search values)
        display_artist: displayArtist.trim() || undefined,
        display_title: displayTitle.trim() || undefined,
      })
      setSearchArtist("")
      setSearchTitle("")
      setDisplayArtist("")
      setDisplayTitle("")
      onJobCreated()
      showCreditDeductedToast()
    } catch (err) {
      if (err instanceof ApiError && err.status === 402) {
        setIsCreditError(true)
        setError(t('outOfCredits'))
      } else if (err instanceof ApiError) {
        setError(err.message)
      } else {
        setError(t('failedToCreateJob'))
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (file) {
      setUploadFile(file)
      setError("")
    }
  }

  return (
    <>
    <Tabs value={activeTab} onValueChange={setActiveTab}>
      {/* Zero-credit warning banner */}
      {noCredits && (
        <div className="flex items-start gap-2 rounded-lg p-3 mb-4 border border-amber-500/30 bg-amber-500/10">
          <AlertTriangle className="w-5 h-5 text-amber-500 shrink-0 mt-0.5" />
          <div className="text-sm" style={{ color: 'var(--text)' }}>
            <p>{tFlow('noCreditsWarning')}</p>
            <button
              onClick={() => setShowBuyCreditsDialog(true)}
              className="inline-block mt-1 text-sm font-medium underline"
              style={{ color: 'var(--brand-pink)' }}
            >
              {tFlow('buyCredits')}
            </button>
          </div>
        </div>
      )}

      {/* Only show tabs bar when there are multiple options */}
      {availableTabs.length > 1 && (
        <TabsList
          className={`grid w-full h-auto rounded-lg p-1 ${
            availableTabs.length === 2 ? 'grid-cols-2' : 'grid-cols-3'
          }`}
          style={{ backgroundColor: 'var(--secondary)' }}
        >
          {availableTabs.includes("search") && (
            <TabsTrigger
              value="search"
              className="gap-1.5 sm:gap-2 py-3 min-h-[44px] text-xs sm:text-sm rounded-md data-[state=active]:!bg-[var(--brand-pink)] data-[state=active]:!text-white data-[state=active]:shadow-md data-[state=inactive]:hover:bg-[var(--brand-pink)]/10"
              style={{ color: activeTab === 'search' ? 'white' : 'var(--text-muted)' }}
            >
              <Music className="w-4 h-4 shrink-0" />
              <span className="truncate">{t('search')}</span>
            </TabsTrigger>
          )}
          {availableTabs.includes("upload") && (
            <TabsTrigger
              value="upload"
              className="gap-1.5 sm:gap-2 py-3 min-h-[44px] text-xs sm:text-sm rounded-md data-[state=active]:!bg-[var(--brand-pink)] data-[state=active]:!text-white data-[state=active]:shadow-md data-[state=inactive]:hover:bg-[var(--brand-pink)]/10"
              style={{ color: activeTab === 'upload' ? 'white' : 'var(--text-muted)' }}
            >
              <Upload className="w-4 h-4 shrink-0" />
              <span className="truncate">{t('upload')}</span>
            </TabsTrigger>
          )}
          {availableTabs.includes("url") && (
            <TabsTrigger
              value="url"
              className="gap-1.5 sm:gap-2 py-3 min-h-[44px] text-xs sm:text-sm rounded-md data-[state=active]:!bg-[var(--brand-pink)] data-[state=active]:!text-white data-[state=active]:shadow-md data-[state=inactive]:hover:bg-[var(--brand-pink)]/10"
              style={{ color: activeTab === 'url' ? 'white' : 'var(--text-muted)' }}
            >
              <Youtube className="w-4 h-4 shrink-0" />
              <span className="truncate">{t('url')}</span>
            </TabsTrigger>
          )}
        </TabsList>
      )}

      {availableTabs.includes("upload") && (
        <TabsContent value="upload" className={availableTabs.length === 1 ? "" : "mt-4"}>
          <form onSubmit={handleUploadSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="audio-file" style={{ color: 'var(--text)' }}>{t('audioFile')}</Label>
            <div
              className="relative border-2 border-dashed rounded-lg p-6 text-center transition-colors cursor-pointer"
              style={{
                borderColor: uploadFile ? 'rgba(255, 122, 204, 0.5)' : 'var(--card-border)',
                backgroundColor: uploadFile ? 'rgba(255, 122, 204, 0.05)' : 'var(--secondary)',
              }}
            >
              <Input
                id="audio-file"
                type="file"
                accept=".mp3,.wav,.flac,.m4a,.ogg,audio/*"
                onChange={handleFileChange}
                className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                disabled={isSubmitting}
              />
              <Upload className="w-8 h-8 mx-auto mb-2" style={{ color: 'var(--text-muted)' }} />
              {uploadFile ? (
                <div>
                  <p className="font-medium" style={{ color: 'var(--text)' }}>{uploadFile.name}</p>
                  <p className="text-sm" style={{ color: 'var(--text-muted)' }}>{(uploadFile.size / 1024 / 1024).toFixed(2)} MB</p>
                </div>
              ) : (
                <div>
                  <p className="font-medium mb-1" style={{ color: 'var(--text)' }}>{t('clickToUpload')}</p>
                  <p className="text-sm" style={{ color: 'var(--text-muted)' }}>{t('supportedFormats')}</p>
                </div>
              )}
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
            <div className="space-y-2">
              <Label htmlFor="upload-artist" style={{ color: 'var(--text)' }}>{tFlow('artist')}</Label>
              <AutocompleteInput
                id="upload-artist"
                placeholder="Artist name"
                value={uploadArtist}
                onChange={setUploadArtist}
                fetchSuggestions={fetchArtistSuggestions}
                disabled={isSubmitting}
                style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="upload-title" style={{ color: 'var(--text)' }}>{tFlow('title')}</Label>
              <AutocompleteInput
                id="upload-title"
                placeholder="Song title"
                value={uploadTitle}
                onChange={setUploadTitle}
                fetchSuggestions={fetchTrackSuggestions(uploadArtist)}
                disabled={isSubmitting}
                style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
              />
            </div>
          </div>
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
            <span className="text-amber-500 font-medium">Tip:</span> {t('artistTip')}
          </p>

          {/* Private (no YouTube upload) mode */}
          <div className="space-y-2 pt-4 border-t" style={{ borderColor: 'var(--card-border)' }}>
            <div className="flex items-start gap-3">
              <input
                type="checkbox"
                id="private-upload"
                checked={isPrivate}
                onChange={(e) => setIsPrivate(e.target.checked)}
                disabled={isSubmitting}
                className="mt-1 w-4 h-4 rounded border-border bg-secondary accent-[var(--brand-pink)] focus:ring-[var(--brand-pink)] focus:ring-offset-background"
              />
              <div>
                <Label htmlFor="private-upload" className="cursor-pointer" style={{ color: 'var(--text)' }}>
                  {t('privateNoYoutubeUpload')}
                </Label>
                <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
                  {t('filesDropboxOnly')}
                </p>
              </div>
            </div>
          </div>

          {error && activeTab === "upload" && (
            <div className="text-sm text-red-400 bg-red-500/10 rounded p-2">
              <p>{error}</p>
              {isCreditError && (
                <button onClick={() => setShowBuyCreditsDialog(true)} className="inline-block mt-1 font-medium underline" style={{ color: 'var(--brand-pink)' }}>Buy Credits</button>
              )}
            </div>
          )}

          <Button
            type="submit"
            className="w-full bg-[var(--brand-pink)] hover:bg-[var(--brand-pink-hover)] text-white shadow-[0_0_15px_rgba(255,122,204,0.3)] hover:shadow-[0_0_20px_rgba(255,122,204,0.5)]"
            disabled={isSubmitting || noCredits}
          >
            {isSubmitting ? (
              <>
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                {t('creatingJob')}
              </>
            ) : (
              tFlow('createKaraokeVideo')
            )}
          </Button>
          </form>
        </TabsContent>
      )}

      {availableTabs.includes("url") && (
        <TabsContent value="url" className={availableTabs.length === 1 ? "" : "mt-4"}>
          <form onSubmit={handleUrlSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="youtube-url" style={{ color: 'var(--text)' }}>{t('youtubeUrlLabel')}</Label>
            <div className="relative">
              <Youtube className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4" style={{ color: 'var(--text-muted)' }} />
              <Input
                id="youtube-url"
                type="url"
                placeholder={t('youtubeUrlPlaceholder')}
                value={youtubeUrl}
                onChange={(e) => setYoutubeUrl(e.target.value)}
                className="pl-10"
                style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
                disabled={isSubmitting}
              />
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
            <div className="space-y-2">
              <Label htmlFor="youtube-artist" style={{ color: 'var(--text)' }}>{tFlow('artist')}</Label>
              <AutocompleteInput
                id="youtube-artist"
                placeholder="Artist name"
                value={youtubeArtist}
                onChange={setYoutubeArtist}
                fetchSuggestions={fetchArtistSuggestions}
                disabled={isSubmitting}
                style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="youtube-title" style={{ color: 'var(--text)' }}>{tFlow('title')}</Label>
              <AutocompleteInput
                id="youtube-title"
                placeholder="Song title"
                value={youtubeTitle}
                onChange={setYoutubeTitle}
                fetchSuggestions={fetchTrackSuggestions(youtubeArtist)}
                disabled={isSubmitting}
                style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
              />
            </div>
          </div>
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
            <span className="text-amber-500 font-medium">Tip:</span> {t('artistTip')}
          </p>

          {/* Private (no YouTube upload) mode */}
          <div className="space-y-2 pt-4 border-t" style={{ borderColor: 'var(--card-border)' }}>
            <div className="flex items-start gap-3">
              <input
                type="checkbox"
                id="private-url"
                checked={isPrivate}
                onChange={(e) => setIsPrivate(e.target.checked)}
                disabled={isSubmitting}
                className="mt-1 w-4 h-4 rounded border-border bg-secondary accent-[var(--brand-pink)] focus:ring-[var(--brand-pink)] focus:ring-offset-background"
              />
              <div>
                <Label htmlFor="private-url" className="cursor-pointer" style={{ color: 'var(--text)' }}>
                  {t('privateNoYoutubeUpload')}
                </Label>
                <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
                  {t('filesDropboxOnly')}
                </p>
              </div>
            </div>
          </div>

          {error && activeTab === "url" && (
            <div className="text-sm text-red-400 bg-red-500/10 rounded p-2">
              <p>{error}</p>
              {isCreditError && (
                <button onClick={() => setShowBuyCreditsDialog(true)} className="inline-block mt-1 font-medium underline" style={{ color: 'var(--brand-pink)' }}>Buy Credits</button>
              )}
            </div>
          )}

          <Button
            type="submit"
            className="w-full bg-[var(--brand-pink)] hover:bg-[var(--brand-pink-hover)] text-white shadow-[0_0_15px_rgba(255,122,204,0.3)] hover:shadow-[0_0_20px_rgba(255,122,204,0.5)]"
            disabled={isSubmitting || noCredits}
          >
            {isSubmitting ? (
              <>
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                {t('creatingJob')}
              </>
            ) : (
              tFlow('createKaraokeVideo')
            )}
            </Button>
          </form>
        </TabsContent>
      )}

      {availableTabs.includes("search") && (
        <TabsContent value="search" className={availableTabs.length === 1 ? "" : "mt-4"}>
          <form onSubmit={handleSearchSubmit} className="space-y-4">
            {/* Search For section */}
            <div className="space-y-2">
              <Label style={{ color: 'var(--text)' }}>{t('searchForAudioOnline')}</Label>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
              <div className="space-y-2">
                <Label htmlFor="search-artist" className="text-xs" style={{ color: 'var(--text-muted)' }}>{tFlow('artist')}</Label>
                <AutocompleteInput
                  id="search-artist"
                  data-testid="search-artist-input"
                  placeholder="Artist name"
                  value={searchArtist}
                  onChange={setSearchArtist}
                  fetchSuggestions={fetchArtistSuggestions}
                  disabled={isSubmitting}
                  style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="search-title" className="text-xs" style={{ color: 'var(--text-muted)' }}>{tFlow('title')}</Label>
                <AutocompleteInput
                  id="search-title"
                  data-testid="search-title-input"
                  placeholder="Song title"
                  value={searchTitle}
                  onChange={setSearchTitle}
                  fetchSuggestions={fetchTrackSuggestions(searchArtist)}
                  disabled={isSubmitting}
                  style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
                />
              </div>
            </div>
          </div>
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
            <span className="text-amber-500 font-medium">Tip:</span> {t('artistTip')}
          </p>

          {/* Community version check banner */}
          {communityData?.has_community && !communityDismissed && (
            <CommunityVersionBanner
              data={communityData}
              onDismiss={() => setCommunityDismissed(true)}
            />
          )}

          {/* Display As toggle (optional) */}
          <div className="space-y-2">
            <button
              type="button"
              onClick={() => setShowDisplayAs(!showDisplayAs)}
              className="flex items-center gap-2 text-sm hover:opacity-80 transition-opacity"
              style={{ color: 'var(--text-muted)' }}
              disabled={isSubmitting}
            >
              {showDisplayAs ? (
                <ChevronDown className="w-4 h-4" />
              ) : (
                <ChevronRight className="w-4 h-4" />
              )}
              {t('useDisplayAs')}
            </button>
            {showDisplayAs && (
              <div className="space-y-2 pl-6">
                <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
                  Override how artist/title appear on the title screen and filename, e.g. for soundtracks.
                </p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
                  <div className="space-y-2">
                    <Label htmlFor="display-artist" className="text-xs" style={{ color: 'var(--text-muted)' }}>{t('displayArtist')}</Label>
                    <Input
                      id="display-artist"
                      placeholder={t('displayArtistPlaceholder')}
                      value={displayArtist}
                      onChange={(e) => setDisplayArtist(e.target.value)}
                      disabled={isSubmitting}
                      style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="display-title" className="text-xs" style={{ color: 'var(--text-muted)' }}>{t('displayTitle')}</Label>
                    <Input
                      id="display-title"
                      placeholder={t('displayTitlePlaceholder')}
                      value={displayTitle}
                      onChange={(e) => setDisplayTitle(e.target.value)}
                      disabled={isSubmitting}
                      style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
                    />
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Private (no YouTube upload) mode */}
          <div className="space-y-2 pt-4 border-t" style={{ borderColor: 'var(--card-border)' }}>
            <div className="flex items-start gap-3">
              <input
                type="checkbox"
                id="private-search"
                checked={isPrivate}
                onChange={(e) => setIsPrivate(e.target.checked)}
                disabled={isSubmitting}
                className="mt-1 w-4 h-4 rounded border-border bg-secondary accent-[var(--brand-pink)] focus:ring-[var(--brand-pink)] focus:ring-offset-background"
              />
              <div>
                <Label htmlFor="private-search" className="cursor-pointer" style={{ color: 'var(--text)' }}>
                  {t('privateNoYoutubeUpload')}
                </Label>
                <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
                  {t('filesDropboxOnly')}
                </p>
              </div>
            </div>
          </div>

          {error && activeTab === "search" && (
            <div className="text-sm text-red-400 bg-red-500/10 rounded p-2">
              <p>{error}</p>
              {isCreditError && (
                <button onClick={() => setShowBuyCreditsDialog(true)} className="inline-block mt-1 font-medium underline" style={{ color: 'var(--brand-pink)' }}>Buy Credits</button>
              )}
            </div>
          )}

          <Button
            type="submit"
            className="w-full bg-[var(--brand-pink)] hover:bg-[var(--brand-pink-hover)] text-white shadow-[0_0_15px_rgba(255,122,204,0.3)] hover:shadow-[0_0_20px_rgba(255,122,204,0.5)]"
            disabled={isSubmitting || noCredits}
          >
            {isSubmitting ? (
              <>
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                {t('searching')}
              </>
            ) : (
              t('searchCreateJob')
            )}
            </Button>
          </form>
        </TabsContent>
      )}
    </Tabs>
    <BuyCreditsDialog open={showBuyCreditsDialog} onClose={() => setShowBuyCreditsDialog(false)} />
    </>
  )
}

