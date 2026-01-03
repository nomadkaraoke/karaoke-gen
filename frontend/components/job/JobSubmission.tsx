"use client"

import { useState } from "react"
import { api, ApiError } from "@/lib/api"
import type { ColorOverrides } from "@/lib/video-themes"
import { cleanColorOverrides } from "@/lib/video-themes"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ThemeSelector } from "./ThemeSelector"
import { ColorOverridesPanel } from "./ColorOverrides"
import { Upload, Youtube, Music, Loader2 } from "lucide-react"

interface JobSubmissionProps {
  onJobCreated: () => void
}

export function JobSubmission({ onJobCreated }: JobSubmissionProps) {
  const [activeTab, setActiveTab] = useState("search")
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

  // Theme selection (shared across all tabs)
  const [selectedTheme, setSelectedTheme] = useState<string | undefined>()
  const [colorOverrides, setColorOverrides] = useState<ColorOverrides>({})

  // Non-interactive mode (shared across all tabs)
  const [nonInteractive, setNonInteractive] = useState(false)

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
        theme_id: selectedTheme,
        color_overrides: cleanColorOverrides(colorOverrides),
        non_interactive: nonInteractive,
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

    setIsSubmitting(true)
    try {
      await api.createJobFromUrl(
        youtubeUrl.trim(),
        youtubeArtist.trim() || undefined,
        youtubeTitle.trim() || undefined,
        {
          theme_id: selectedTheme,
          color_overrides: cleanColorOverrides(colorOverrides),
          non_interactive: nonInteractive,
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
        theme_id: selectedTheme,
        color_overrides: cleanColorOverrides(colorOverrides),
        non_interactive: nonInteractive,
      })
      setSearchArtist("")
      setSearchTitle("")
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
      <TabsList className="grid w-full grid-cols-3 h-auto rounded-lg p-1" style={{ backgroundColor: 'var(--secondary)' }}>
        <TabsTrigger
          value="search"
          className="gap-1.5 sm:gap-2 py-3 min-h-[44px] text-xs sm:text-sm rounded-md data-[state=active]:!bg-amber-600 data-[state=active]:!text-white data-[state=active]:shadow-md data-[state=inactive]:hover:bg-amber-600/10"
          style={{ color: activeTab === 'search' ? 'white' : 'var(--text-muted)' }}
        >
          <Music className="w-4 h-4 shrink-0" />
          <span className="truncate">Search</span>
        </TabsTrigger>
        <TabsTrigger
          value="upload"
          className="gap-1.5 sm:gap-2 py-3 min-h-[44px] text-xs sm:text-sm rounded-md data-[state=active]:!bg-amber-600 data-[state=active]:!text-white data-[state=active]:shadow-md data-[state=inactive]:hover:bg-amber-600/10"
          style={{ color: activeTab === 'upload' ? 'white' : 'var(--text-muted)' }}
        >
          <Upload className="w-4 h-4 shrink-0" />
          <span className="truncate">Upload</span>
        </TabsTrigger>
        <TabsTrigger
          value="url"
          className="gap-1.5 sm:gap-2 py-3 min-h-[44px] text-xs sm:text-sm rounded-md data-[state=active]:!bg-amber-600 data-[state=active]:!text-white data-[state=active]:shadow-md data-[state=inactive]:hover:bg-amber-600/10"
          style={{ color: activeTab === 'url' ? 'white' : 'var(--text-muted)' }}
        >
          <Youtube className="w-4 h-4 shrink-0" />
          <span className="truncate">URL</span>
        </TabsTrigger>
      </TabsList>

      <TabsContent value="upload" className="mt-4">
        <form onSubmit={handleUploadSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="audio-file" style={{ color: 'var(--text)' }}>Audio File</Label>
            <div
              className="relative border-2 border-dashed rounded-lg p-6 text-center transition-colors cursor-pointer"
              style={{
                borderColor: uploadFile ? 'rgb(245 158 11 / 0.5)' : 'var(--card-border)',
                backgroundColor: uploadFile ? 'rgb(245 158 11 / 0.05)' : 'var(--secondary)',
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

          {/* Theme Selection */}
          <div className="space-y-4 pt-4 border-t" style={{ borderColor: 'var(--card-border)' }}>
            <ThemeSelector
              value={selectedTheme}
              onChange={setSelectedTheme}
              disabled={isSubmitting}
            />
            <ColorOverridesPanel
              value={colorOverrides}
              onChange={setColorOverrides}
              disabled={isSubmitting}
            />
          </div>

          {/* Non-interactive mode */}
          <div className="space-y-2 pt-4 border-t" style={{ borderColor: 'var(--card-border)' }}>
            <div className="flex items-start gap-3">
              <input
                type="checkbox"
                id="non-interactive-upload"
                checked={nonInteractive}
                onChange={(e) => setNonInteractive(e.target.checked)}
                disabled={isSubmitting}
                className="mt-1 w-4 h-4 rounded border-gray-600 bg-gray-700 text-amber-500 focus:ring-amber-500 focus:ring-offset-gray-800"
              />
              <div>
                <Label htmlFor="non-interactive-upload" className="cursor-pointer" style={{ color: 'var(--text)' }}>
                  Skip lyrics review (non-interactive)
                </Label>
                <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
                  <span className="text-amber-500 font-medium">Warning:</span> AI lyrics correction isn&apos;t perfect.
                  Skipping review will likely result in timing errors and incorrect words in your karaoke video.
                </p>
              </div>
            </div>
          </div>

          {error && activeTab === "upload" && (
            <p className="text-sm text-red-400 bg-red-500/10 rounded p-2">{error}</p>
          )}

          <Button
            type="submit"
            className="w-full bg-amber-600 hover:bg-amber-500 text-white"
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

      <TabsContent value="url" className="mt-4">
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
              <Label htmlFor="youtube-artist" style={{ color: 'var(--text)' }}>Artist (optional)</Label>
              <Input
                id="youtube-artist"
                placeholder="Auto-detected"
                value={youtubeArtist}
                onChange={(e) => setYoutubeArtist(e.target.value)}
                disabled={isSubmitting}
                style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="youtube-title" style={{ color: 'var(--text)' }}>Title (optional)</Label>
              <Input
                id="youtube-title"
                placeholder="Auto-detected"
                value={youtubeTitle}
                onChange={(e) => setYoutubeTitle(e.target.value)}
                disabled={isSubmitting}
                style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
              />
            </div>
          </div>
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
            <span className="text-amber-500 font-medium">Note:</span> Override auto-detected values if needed. These appear on the title card and video filename.
          </p>

          {/* Theme Selection */}
          <div className="space-y-4 pt-4 border-t" style={{ borderColor: 'var(--card-border)' }}>
            <ThemeSelector
              value={selectedTheme}
              onChange={setSelectedTheme}
              disabled={isSubmitting}
            />
            <ColorOverridesPanel
              value={colorOverrides}
              onChange={setColorOverrides}
              disabled={isSubmitting}
            />
          </div>

          {/* Non-interactive mode */}
          <div className="space-y-2 pt-4 border-t" style={{ borderColor: 'var(--card-border)' }}>
            <div className="flex items-start gap-3">
              <input
                type="checkbox"
                id="non-interactive-url"
                checked={nonInteractive}
                onChange={(e) => setNonInteractive(e.target.checked)}
                disabled={isSubmitting}
                className="mt-1 w-4 h-4 rounded border-gray-600 bg-gray-700 text-amber-500 focus:ring-amber-500 focus:ring-offset-gray-800"
              />
              <div>
                <Label htmlFor="non-interactive-url" className="cursor-pointer" style={{ color: 'var(--text)' }}>
                  Skip lyrics review (non-interactive)
                </Label>
                <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
                  <span className="text-amber-500 font-medium">Warning:</span> AI lyrics correction isn&apos;t perfect.
                  Skipping review will likely result in timing errors and incorrect words in your karaoke video.
                </p>
              </div>
            </div>
          </div>

          {error && activeTab === "url" && (
            <p className="text-sm text-red-400 bg-red-500/10 rounded p-2">{error}</p>
          )}

          <Button
            type="submit"
            className="w-full bg-amber-600 hover:bg-amber-500 text-white"
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

      <TabsContent value="search" className="mt-4">
        <form onSubmit={handleSearchSubmit} className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 sm:gap-4">
            <div className="space-y-2">
              <Label htmlFor="search-artist" style={{ color: 'var(--text)' }}>Artist</Label>
              <Input
                id="search-artist"
                placeholder="Artist name"
                value={searchArtist}
                onChange={(e) => setSearchArtist(e.target.value)}
                disabled={isSubmitting}
                style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="search-title" style={{ color: 'var(--text)' }}>Title</Label>
              <Input
                id="search-title"
                placeholder="Song title"
                value={searchTitle}
                onChange={(e) => setSearchTitle(e.target.value)}
                disabled={isSubmitting}
                style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
              />
            </div>
          </div>
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
            <span className="text-amber-500 font-medium">Note:</span> Format these exactly as you want them on the title card and video filename.
          </p>

          {/* Theme Selection */}
          <div className="space-y-4 pt-4 border-t" style={{ borderColor: 'var(--card-border)' }}>
            <ThemeSelector
              value={selectedTheme}
              onChange={setSelectedTheme}
              disabled={isSubmitting}
            />
            <ColorOverridesPanel
              value={colorOverrides}
              onChange={setColorOverrides}
              disabled={isSubmitting}
            />
          </div>

          {/* Non-interactive mode */}
          <div className="space-y-2 pt-4 border-t" style={{ borderColor: 'var(--card-border)' }}>
            <div className="flex items-start gap-3">
              <input
                type="checkbox"
                id="non-interactive-search"
                checked={nonInteractive}
                onChange={(e) => setNonInteractive(e.target.checked)}
                disabled={isSubmitting}
                className="mt-1 w-4 h-4 rounded border-gray-600 bg-gray-700 text-amber-500 focus:ring-amber-500 focus:ring-offset-gray-800"
              />
              <div>
                <Label htmlFor="non-interactive-search" className="cursor-pointer" style={{ color: 'var(--text)' }}>
                  Skip lyrics review (non-interactive)
                </Label>
                <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
                  <span className="text-amber-500 font-medium">Warning:</span> AI lyrics correction isn&apos;t perfect.
                  Skipping review will likely result in timing errors and incorrect words in your karaoke video.
                </p>
              </div>
            </div>
          </div>

          {error && activeTab === "search" && (
            <p className="text-sm text-red-400 bg-red-500/10 rounded p-2">{error}</p>
          )}

          <Button
            type="submit"
            className="w-full bg-amber-600 hover:bg-amber-500 text-white"
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
    </Tabs>
  )
}

