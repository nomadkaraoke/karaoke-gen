"use client"

import { useState, useEffect, useMemo } from "react"
import { api, ApiError } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { useTenant } from "@/lib/tenant"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Upload, Youtube, Music, Loader2, ChevronDown, ChevronRight } from "lucide-react"

interface JobSubmissionProps {
  onJobCreated: () => void
}

export function JobSubmission({ onJobCreated }: JobSubmissionProps) {
  const { user } = useAuth()
  const { features, isDefault, tenant } = useTenant()
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

  // Non-interactive mode (shared across all tabs)
  const [nonInteractive, setNonInteractive] = useState(false)

  // Private (non-published) mode - Dropbox only, no YouTube/GDrive
  const [isPrivate, setIsPrivate] = useState(false)

  async function handleUploadSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError("")

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
        non_interactive: nonInteractive,
        is_private: isPrivate,
      })
      setUploadFile(null)
      setUploadArtist("")
      setUploadTitle("")
      onJobCreated()
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message)
      } else {
        setError("Failed to create job")
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  async function handleUrlSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError("")

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
          non_interactive: nonInteractive,
          is_private: isPrivate,
        }
      )
      setYoutubeUrl("")
      setYoutubeArtist("")
      setYoutubeTitle("")
      onJobCreated()
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message)
      } else {
        setError("Failed to create job")
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  async function handleSearchSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError("")

    if (!searchArtist.trim() || !searchTitle.trim()) {
      setError("Please enter both artist and title")
      return
    }

    setIsSubmitting(true)
    try {
      await api.searchAudio(searchArtist.trim(), searchTitle.trim(), false, {
        non_interactive: nonInteractive,
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
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message)
      } else {
        setError("Failed to search for audio")
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
    <Tabs value={activeTab} onValueChange={setActiveTab}>
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
              <span className="truncate">Search</span>
            </TabsTrigger>
          )}
          {availableTabs.includes("upload") && (
            <TabsTrigger
              value="upload"
              className="gap-1.5 sm:gap-2 py-3 min-h-[44px] text-xs sm:text-sm rounded-md data-[state=active]:!bg-[var(--brand-pink)] data-[state=active]:!text-white data-[state=active]:shadow-md data-[state=inactive]:hover:bg-[var(--brand-pink)]/10"
              style={{ color: activeTab === 'upload' ? 'white' : 'var(--text-muted)' }}
            >
              <Upload className="w-4 h-4 shrink-0" />
              <span className="truncate">Upload</span>
            </TabsTrigger>
          )}
          {availableTabs.includes("url") && (
            <TabsTrigger
              value="url"
              className="gap-1.5 sm:gap-2 py-3 min-h-[44px] text-xs sm:text-sm rounded-md data-[state=active]:!bg-[var(--brand-pink)] data-[state=active]:!text-white data-[state=active]:shadow-md data-[state=inactive]:hover:bg-[var(--brand-pink)]/10"
              style={{ color: activeTab === 'url' ? 'white' : 'var(--text-muted)' }}
            >
              <Youtube className="w-4 h-4 shrink-0" />
              <span className="truncate">URL</span>
            </TabsTrigger>
          )}
        </TabsList>
      )}

      {availableTabs.includes("upload") && (
        <TabsContent value="upload" className={availableTabs.length === 1 ? "" : "mt-4"}>
          <form onSubmit={handleUploadSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="audio-file" style={{ color: 'var(--text)' }}>Audio File</Label>
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
                  <p className="font-medium mb-1" style={{ color: 'var(--text)' }}>Click to upload</p>
                  <p className="text-sm" style={{ color: 'var(--text-muted)' }}>MP3, WAV, FLAC, M4A, or OGG</p>
                </div>
              )}
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
            <div className="space-y-2">
              <Label htmlFor="upload-artist" style={{ color: 'var(--text)' }}>Artist</Label>
              <Input
                id="upload-artist"
                placeholder="Artist name"
                value={uploadArtist}
                onChange={(e) => setUploadArtist(e.target.value)}
                disabled={isSubmitting}
                style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="upload-title" style={{ color: 'var(--text)' }}>Title</Label>
              <Input
                id="upload-title"
                placeholder="Song title"
                value={uploadTitle}
                onChange={(e) => setUploadTitle(e.target.value)}
                disabled={isSubmitting}
                style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
              />
            </div>
          </div>
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
            <span className="text-amber-500 font-medium">Note:</span> Format these exactly as you want them on the title card and video filename.
          </p>

          {/* Non-interactive mode (admin only) */}
          {isAdmin && (
            <div className="space-y-2 pt-4 border-t" style={{ borderColor: 'var(--card-border)' }}>
              <div className="flex items-start gap-3">
                <input
                  type="checkbox"
                  id="non-interactive-upload"
                  checked={nonInteractive}
                  onChange={(e) => setNonInteractive(e.target.checked)}
                  disabled={isSubmitting}
                  className="mt-1 w-4 h-4 rounded border-border bg-secondary accent-[var(--brand-pink)] focus:ring-[var(--brand-pink)] focus:ring-offset-background"
                />
                <div>
                  <Label htmlFor="non-interactive-upload" className="cursor-pointer" style={{ color: 'var(--text)' }}>
                    Skip lyrics review (non-interactive)
                  </Label>
                  <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
                    <span className="text-amber-500 font-medium">Warning:</span> auto lyrics correction isn&apos;t perfect.
                    Skipping review will likely result in incorrect words in your karaoke video.
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* Private (non-published) mode */}
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
                  Private (non-published)
                </Label>
                <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
                  Files delivered via Dropbox only. No YouTube or Google Drive upload.
                </p>
              </div>
            </div>
          </div>

          {error && activeTab === "upload" && (
            <p className="text-sm text-red-400 bg-red-500/10 rounded p-2">{error}</p>
          )}

          <Button
            type="submit"
            className="w-full bg-[var(--brand-pink)] hover:bg-[var(--brand-pink-hover)] text-white shadow-[0_0_15px_rgba(255,122,204,0.3)] hover:shadow-[0_0_20px_rgba(255,122,204,0.5)]"
            disabled={isSubmitting}
          >
            {isSubmitting ? (
              <>
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                Creating Job...
              </>
            ) : (
              "Create Karaoke Video"
            )}
          </Button>
          </form>
        </TabsContent>
      )}

      {availableTabs.includes("url") && (
        <TabsContent value="url" className={availableTabs.length === 1 ? "" : "mt-4"}>
          <form onSubmit={handleUrlSubmit} className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="youtube-url" style={{ color: 'var(--text)' }}>YouTube URL</Label>
            <div className="relative">
              <Youtube className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4" style={{ color: 'var(--text-muted)' }} />
              <Input
                id="youtube-url"
                type="url"
                placeholder="https://youtube.com/watch?v=..."
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
              <Label htmlFor="youtube-artist" style={{ color: 'var(--text)' }}>Artist</Label>
              <Input
                id="youtube-artist"
                placeholder="Artist name"
                value={youtubeArtist}
                onChange={(e) => setYoutubeArtist(e.target.value)}
                disabled={isSubmitting}
                style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="youtube-title" style={{ color: 'var(--text)' }}>Title</Label>
              <Input
                id="youtube-title"
                placeholder="Song title"
                value={youtubeTitle}
                onChange={(e) => setYoutubeTitle(e.target.value)}
                disabled={isSubmitting}
                style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
              />
            </div>
          </div>
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
            <span className="text-amber-500 font-medium">Note:</span> Format these exactly as you want them on the title card and video filename.
          </p>

          {/* Non-interactive mode (admin only) */}
          {isAdmin && (
            <div className="space-y-2 pt-4 border-t" style={{ borderColor: 'var(--card-border)' }}>
              <div className="flex items-start gap-3">
                <input
                  type="checkbox"
                  id="non-interactive-url"
                  checked={nonInteractive}
                  onChange={(e) => setNonInteractive(e.target.checked)}
                  disabled={isSubmitting}
                  className="mt-1 w-4 h-4 rounded border-border bg-secondary accent-[var(--brand-pink)] focus:ring-[var(--brand-pink)] focus:ring-offset-background"
                />
                <div>
                  <Label htmlFor="non-interactive-url" className="cursor-pointer" style={{ color: 'var(--text)' }}>
                    Skip lyrics review (non-interactive)
                  </Label>
                  <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
                    <span className="text-amber-500 font-medium">Warning:</span> auto lyrics correction isn&apos;t perfect.
                    Skipping review will likely result in incorrect words in your karaoke video.
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* Private (non-published) mode */}
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
                  Private (non-published)
                </Label>
                <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
                  Files delivered via Dropbox only. No YouTube or Google Drive upload.
                </p>
              </div>
            </div>
          </div>

          {error && activeTab === "url" && (
            <p className="text-sm text-red-400 bg-red-500/10 rounded p-2">{error}</p>
          )}

          <Button
            type="submit"
            className="w-full bg-[var(--brand-pink)] hover:bg-[var(--brand-pink-hover)] text-white shadow-[0_0_15px_rgba(255,122,204,0.3)] hover:shadow-[0_0_20px_rgba(255,122,204,0.5)]"
            disabled={isSubmitting}
          >
            {isSubmitting ? (
              <>
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                Creating Job...
              </>
            ) : (
              "Create Karaoke Video"
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
              <Label style={{ color: 'var(--text)' }}>Search For Audio Online</Label>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
              <div className="space-y-2">
                <Label htmlFor="search-artist" className="text-xs" style={{ color: 'var(--text-muted)' }}>Artist</Label>
                <Input
                  id="search-artist"
                  data-testid="search-artist-input"
                  placeholder="Artist name"
                  value={searchArtist}
                  onChange={(e) => setSearchArtist(e.target.value)}
                  disabled={isSubmitting}
                  style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="search-title" className="text-xs" style={{ color: 'var(--text-muted)' }}>Title</Label>
                <Input
                  id="search-title"
                  data-testid="search-title-input"
                  placeholder="Song title"
                  value={searchTitle}
                  onChange={(e) => setSearchTitle(e.target.value)}
                  disabled={isSubmitting}
                  style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
                />
              </div>
            </div>
          </div>
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
            <span className="text-amber-500 font-medium">Note:</span> Format these exactly as you want them on the title card and video filename.
          </p>

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
              Use different artist/title for title screen
            </button>
            {showDisplayAs && (
              <div className="space-y-2 pl-6">
                <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
                  Override how artist/title appear on the title screen and filename, e.g. for soundtracks.
                </p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
                  <div className="space-y-2">
                    <Label htmlFor="display-artist" className="text-xs" style={{ color: 'var(--text-muted)' }}>Display Artist</Label>
                    <Input
                      id="display-artist"
                      placeholder="e.g., Footloose (Broadway Cast)"
                      value={displayArtist}
                      onChange={(e) => setDisplayArtist(e.target.value)}
                      disabled={isSubmitting}
                      style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="display-title" className="text-xs" style={{ color: 'var(--text-muted)' }}>Display Title</Label>
                    <Input
                      id="display-title"
                      placeholder="e.g., I Can't Stand Still"
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

          {/* Non-interactive mode (admin only) */}
          {isAdmin && (
            <div className="space-y-2 pt-4 border-t" style={{ borderColor: 'var(--card-border)' }}>
              <div className="flex items-start gap-3">
                <input
                  type="checkbox"
                  id="non-interactive-search"
                  checked={nonInteractive}
                  onChange={(e) => setNonInteractive(e.target.checked)}
                  disabled={isSubmitting}
                  className="mt-1 w-4 h-4 rounded border-border bg-secondary accent-[var(--brand-pink)] focus:ring-[var(--brand-pink)] focus:ring-offset-background"
                />
                <div>
                  <Label htmlFor="non-interactive-search" className="cursor-pointer" style={{ color: 'var(--text)' }}>
                    Skip lyrics review (non-interactive)
                  </Label>
                  <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
                    <span className="text-amber-500 font-medium">Warning:</span> auto lyrics correction isn&apos;t perfect.
                    Skipping review will likely result in incorrect words in your karaoke video.
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* Private (non-published) mode */}
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
                  Private (non-published)
                </Label>
                <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
                  Files delivered via Dropbox only. No YouTube or Google Drive upload.
                </p>
              </div>
            </div>
          </div>

          {error && activeTab === "search" && (
            <p className="text-sm text-red-400 bg-red-500/10 rounded p-2">{error}</p>
          )}

          <Button
            type="submit"
            className="w-full bg-[var(--brand-pink)] hover:bg-[var(--brand-pink-hover)] text-white shadow-[0_0_15px_rgba(255,122,204,0.3)] hover:shadow-[0_0_20px_rgba(255,122,204,0.5)]"
            disabled={isSubmitting}
          >
            {isSubmitting ? (
              <>
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                Searching...
              </>
            ) : (
              "Search & Create Job"
            )}
            </Button>
          </form>
        </TabsContent>
      )}
    </Tabs>
  )
}

