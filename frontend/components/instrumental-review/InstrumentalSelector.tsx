"use client"

import { useState, useEffect, useRef, useCallback, useMemo } from "react"
import { useRouter, Link } from "@/i18n/routing"
import { useTranslations } from 'next-intl'
import { Button } from "@/components/ui/button"
import { Spinner } from "@/components/ui/spinner"
import { ArrowLeft, Play, Pause, Check } from "lucide-react"
import { toast } from "sonner"
import {
  api,
  lyricsReviewApi,
  type Job,
  type InstrumentalAnalysis,
  type WaveformData,
  type MuteRegion,
  type InstrumentalSelectionType,
} from "@/lib/api"
import { StemComparison, type AudioType } from "./StemComparison"
import { WaveformViewer } from "./WaveformViewer"
import { MuteRegionEditor } from "./MuteRegionEditor"
import { SelectionOptions } from "./SelectionOptions"
import { CustomUpload } from "./CustomUpload"
import { InstrumentalGuidancePanel } from "./InstrumentalGuidancePanel"

interface InstrumentalSelectorProps {
  job: Job
  isLocalMode?: boolean
}

type ZoomLevel = 1 | 2 | 4

// Map audio type to stem type for API
const STEM_TYPE_MAP: Record<AudioType, string> = {
  original: "original",
  backing: "backing_vocals",
  clean: "clean_instrumental",
  with_backing: "with_backing",
  custom: "custom_instrumental",
  uploaded: "uploaded_instrumental",
}

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds)) return "0:00"
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins}:${secs.toString().padStart(2, "0")}`
}

export function InstrumentalSelector({ job, isLocalMode = false }: InstrumentalSelectorProps) {
  const router = useRouter()
  const t = useTranslations('instrumentalReview')
  const audioRef = useRef<HTMLAudioElement>(null)

  // Data state
  const [analysisData, setAnalysisData] = useState<InstrumentalAnalysis | null>(null)
  const [waveformData, setWaveformData] = useState<WaveformData | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Audio state
  const [activeAudio, setActiveAudio] = useState<AudioType>("backing")
  const [isPlaying, setIsPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)

  // Selection state
  const [selectedOption, setSelectedOption] = useState<InstrumentalSelectionType>("with_backing")
  const [muteRegions, setMuteRegions] = useState<MuteRegion[]>([])
  const [hasCustom, setHasCustom] = useState(false)
  const [hasUploaded, setHasUploaded] = useState(false)
  const [uploadedFilename, setUploadedFilename] = useState("")

  // UI state
  const [zoomLevel, setZoomLevel] = useState<ZoomLevel>(1)
  const [customAudioUrl, setCustomAudioUrl] = useState<string | null>(null)
  const [isCreatingCustom, setIsCreatingCustom] = useState(false)
  const [isUploading, setIsUploading] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [isShiftHeld, setIsShiftHeld] = useState(false)
  const [isAudioLoading, setIsAudioLoading] = useState(false)

  // Hover highlight state
  const [hoveredItem, setHoveredItem] = useState<{
    type: "region" | "segment"
    index: number
  } | null>(null)

  // Derived state
  const hasOriginal = analysisData?.has_original ?? false
  const hasWithBacking = !!analysisData?.audio_urls.with_backing
  const audibleSegments = useMemo(
    () => analysisData?.analysis.audible_segments ?? [],
    [analysisData?.analysis.audible_segments]
  )

  const highlightedTimeRange = useMemo(() => {
    if (!hoveredItem) return null
    const source = hoveredItem.type === "region" ? muteRegions : audibleSegments
    const item = source[hoveredItem.index]
    return item ? { start: item.start_seconds, end: item.end_seconds } : null
  }, [hoveredItem, muteRegions, audibleSegments])

  // Toggle play/pause - defined early so it can be used in keyboard shortcuts
  const togglePlayPause = useCallback(() => {
    const audio = audioRef.current
    if (!audio) return

    if (isPlaying) {
      audio.pause()
    } else {
      audio.play().catch(() => {})
    }
  }, [isPlaying])

  // Check if audio separation is complete
  const audioComplete = job.state_data?.audio_complete ?? true // Default true for legacy jobs
  const [waitingForAudio, setWaitingForAudio] = useState(!audioComplete)

  // Poll for audio completion if separation is still in progress
  useEffect(() => {
    if (!waitingForAudio) return

    const pollInterval = setInterval(async () => {
      try {
        const updatedJob = await api.getJob(job.job_id)
        if (updatedJob?.state_data?.audio_complete) {
          setWaitingForAudio(false)
        }
      } catch {
        // Ignore poll errors, keep trying
      }
    }, 5000) // Poll every 5 seconds

    return () => clearInterval(pollInterval)
  }, [waitingForAudio, job.job_id])

  // Fetch data on mount (only when audio is ready)
  useEffect(() => {
    if (waitingForAudio) return

    async function fetchData() {
      try {
        // Use different API based on mode:
        // - Local mode: uses /api/jobs/{id}/... endpoints
        // - Cloud mode: uses /api/review/{id}/... endpoints
        const [analysis, waveform] = await Promise.all([
          isLocalMode
            ? api.getInstrumentalAnalysis(job.job_id)
            : lyricsReviewApi.getInstrumentalAnalysis(job.job_id),
          isLocalMode
            ? api.getWaveformData(job.job_id)
            : lyricsReviewApi.getWaveformData(job.job_id),
        ])

        setAnalysisData(analysis)
        setWaveformData(waveform)

        // Set initial selection based on recommendation
        const recommended = analysis.analysis.recommended_selection
        setSelectedOption(recommended === "clean" ? "clean" : "with_backing")

        // Set duration from waveform data
        const dur = waveform.duration_seconds ?? waveform.duration ?? 0
        setDuration(dur)

        // Check if uploaded instrumental exists
        if (analysis.has_uploaded_instrumental) {
          setHasUploaded(true)
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load data")
      } finally {
        setIsLoading(false)
      }
    }

    fetchData()
  }, [job.job_id, isLocalMode, waitingForAudio])

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Track shift key
      if (e.key === "Shift") {
        setIsShiftHeld(true)
      }

      // Ignore if typing in input
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement
      ) {
        return
      }

      if (e.code === "Space") {
        e.preventDefault()
        togglePlayPause()
      }
    }

    const handleKeyUp = (e: KeyboardEvent) => {
      if (e.key === "Shift") {
        setIsShiftHeld(false)
      }
    }

    document.addEventListener("keydown", handleKeyDown)
    document.addEventListener("keyup", handleKeyUp)
    return () => {
      document.removeEventListener("keydown", handleKeyDown)
      document.removeEventListener("keyup", handleKeyUp)
    }
  }, [togglePlayPause])

  // Audio event handlers - must re-run when loading completes so audio element is available
  useEffect(() => {
    const audio = audioRef.current
    if (!audio) return

    const handlePlay = () => setIsPlaying(true)
    const handlePause = () => setIsPlaying(false)
    const handleEnded = () => setIsPlaying(false)
    const handleLoadedMetadata = () => {
      if (audio.duration && Number.isFinite(audio.duration)) {
        setDuration(audio.duration)
      }
    }

    audio.addEventListener("play", handlePlay)
    audio.addEventListener("pause", handlePause)
    audio.addEventListener("ended", handleEnded)
    audio.addEventListener("loadedmetadata", handleLoadedMetadata)

    return () => {
      audio.removeEventListener("play", handlePlay)
      audio.removeEventListener("pause", handlePause)
      audio.removeEventListener("ended", handleEnded)
      audio.removeEventListener("loadedmetadata", handleLoadedMetadata)
    }
  }, [isLoading]) // Re-run when loading completes to attach handlers to audio element

  // Smooth playhead animation using requestAnimationFrame
  useEffect(() => {
    const audio = audioRef.current
    if (!audio || !isPlaying) return

    let animationId: number

    const updateTime = () => {
      setCurrentTime(audio.currentTime)
      animationId = requestAnimationFrame(updateTime)
    }

    animationId = requestAnimationFrame(updateTime)

    return () => {
      cancelAnimationFrame(animationId)
    }
  }, [isPlaying])

  // Get audio URL for current selection
  const getAudioUrl = useCallback(() => {
    // Custom instrumental uses the signed URL from creation response
    if (activeAudio === "custom" && customAudioUrl) {
      return customAudioUrl
    }
    // For cloud mode, use signed URLs from analysis data
    // For local mode, use the API stream endpoint
    if (!isLocalMode && analysisData?.audio_urls) {
      const urls = analysisData.audio_urls
      switch (activeAudio) {
        case "clean": return urls.clean || ""
        case "with_backing": return urls.with_backing || ""
        case "original": return urls.original || ""
        case "backing": return urls.backing_vocals || ""
        default: return urls.with_backing || urls.clean || ""
      }
    }
    // Local mode: use API stream endpoint
    const stemType = STEM_TYPE_MAP[activeAudio]
    return api.getAudioStreamUrl(job.job_id, stemType)
  }, [job.job_id, activeAudio, isLocalMode, analysisData, customAudioUrl])

  // Handle audio type change
  const handleAudioChange = useCallback(
    (type: AudioType) => {
      const audio = audioRef.current
      if (!audio) return

      const wasPlaying = !audio.paused
      const time = audio.currentTime

      audio.pause()
      setActiveAudio(type)
      setIsAudioLoading(true)

      // Update audio source
      let audioUrl: string
      // Custom instrumental uses the signed URL from creation response
      if (type === "custom" && customAudioUrl) {
        audioUrl = customAudioUrl
      } else if (!isLocalMode && analysisData?.audio_urls) {
        // Cloud mode: use signed URLs from analysis data
        const urls = analysisData.audio_urls
        switch (type) {
          case "clean": audioUrl = urls.clean || ""; break
          case "with_backing": audioUrl = urls.with_backing || ""; break
          case "original": audioUrl = urls.original || ""; break
          case "backing": audioUrl = urls.backing_vocals || ""; break
          default: audioUrl = urls.with_backing || urls.clean || ""
        }
      } else {
        const stemType = STEM_TYPE_MAP[type]
        audioUrl = api.getAudioStreamUrl(job.job_id, stemType)
      }
      audio.src = audioUrl

      const handleLoaded = () => {
        setIsAudioLoading(false)
        audio.currentTime = time
        if (wasPlaying) {
          audio.play().catch(() => {})
        }
      }

      const handleError = () => {
        setIsAudioLoading(false)
        toast.error("Failed to load audio")
        // Restore previous playback state
        if (wasPlaying) {
          audio.play().catch(() => {})
        }
      }

      audio.addEventListener("loadeddata", handleLoaded, { once: true })
      audio.addEventListener("error", handleError, { once: true })
    },
    [job.job_id, isLocalMode, analysisData, customAudioUrl]
  )

  // Seek to time
  const seekTo = useCallback((time: number, autoPlay = true) => {
    const audio = audioRef.current
    if (!audio || !Number.isFinite(time)) return

    audio.currentTime = time
    setCurrentTime(time)

    if (autoPlay && audio.paused) {
      audio.play().catch(() => {})
    }
  }, [])

  // Mute region management
  const addRegion = useCallback((start: number, end: number) => {
    setMuteRegions((prev) => {
      const newRegions = [...prev, { start_seconds: start, end_seconds: end }]
      newRegions.sort((a, b) => a.start_seconds - b.start_seconds)
      return mergeOverlappingRegions(newRegions)
    })
    setHasCustom(false) // Invalidate custom when regions change
  }, [])

  const removeRegion = useCallback((index: number) => {
    setMuteRegions((prev) => prev.filter((_, i) => i !== index))
    setHasCustom(false)
  }, [])

  const clearAllRegions = useCallback(() => {
    setMuteRegions([])
    setHasCustom(false)
  }, [])

  const addSegmentAsRegion = useCallback(
    (index: number) => {
      const segment = audibleSegments[index]
      if (segment) {
        addRegion(segment.start_seconds, segment.end_seconds)
      }
    },
    [audibleSegments, addRegion]
  )

  // Create custom instrumental
  const handleCreateCustom = useCallback(async () => {
    if (muteRegions.length === 0) return

    const audio = audioRef.current
    const wasPlaying = isPlaying
    const time = currentTime

    // Pause while creating
    if (audio && !audio.paused) {
      audio.pause()
    }

    setIsCreatingCustom(true)
    try {
      const result = await api.createCustomInstrumental(job.job_id, muteRegions)
      const audioUrl = result.audio_url || api.getAudioStreamUrl(job.job_id, "custom_instrumental")
      setCustomAudioUrl(audioUrl)
      setHasCustom(true)
      setSelectedOption("custom")
      setActiveAudio("custom")

      // Update audio source with signed URL from response
      if (audio) {
        audio.src = audioUrl
        audio.addEventListener(
          "loadeddata",
          () => {
            audio.currentTime = time
            if (wasPlaying) {
              audio.play().catch(() => {})
            }
          },
          { once: true }
        )
      }

      toast.success("Custom instrumental created")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to create custom")
      // Resume playback on error
      if (wasPlaying && audio) {
        audio.play().catch(() => {})
      }
    } finally {
      setIsCreatingCustom(false)
    }
  }, [job.job_id, muteRegions, isPlaying, currentTime])

  // Upload custom instrumental
  const handleUpload = useCallback(
    async (file: File) => {
      setIsUploading(true)
      try {
        const result = await api.uploadCustomInstrumental(job.job_id, file)
        setHasUploaded(true)
        setUploadedFilename(file.name)
        setActiveAudio("uploaded")
        setSelectedOption("uploaded")

        // Update audio source
        const audio = audioRef.current
        if (audio) {
          const time = audio.currentTime
          audio.src = api.getAudioStreamUrl(job.job_id, "uploaded_instrumental")
          audio.addEventListener(
            "loadeddata",
            () => {
              audio.currentTime = time
            },
            { once: true }
          )
        }

        toast.success(`Uploaded ${file.name} (${result.duration_seconds.toFixed(1)}s)`)
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "Upload failed")
      } finally {
        setIsUploading(false)
      }
    },
    [job.job_id]
  )

  // Success screen state
  const [showSuccess, setShowSuccess] = useState(false)
  const [countdown, setCountdown] = useState(2)

  // Submit selection (final submission: corrections + instrumental)
  const handleSubmit = useCallback(async () => {
    setIsSubmitting(true)
    try {
      // Fetch current correction data and submit with instrumental selection
      // Using the same review API for both local and cloud mode
      // Map "uploaded" to "custom" for the API — both use stems/custom_instrumental in GCS
      const apiSelection = selectedOption === "uploaded" ? "custom" : selectedOption
      const correctionData = await lyricsReviewApi.getCorrectionData(job.job_id)
      await lyricsReviewApi.completeReview(job.job_id, correctionData, apiSelection)

      // Show success screen in both modes
      setShowSuccess(true)
      setCountdown(isLocalMode ? 2 : 3)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to complete review")
      setIsSubmitting(false)
    }
  }, [job.job_id, selectedOption, isLocalMode])

  // Countdown effect for success screen
  useEffect(() => {
    if (!showSuccess) return

    const interval = setInterval(() => {
      setCountdown((prev) => {
        if (prev <= 1) {
          clearInterval(interval)
          if (isLocalMode) {
            window.close()
          } else {
            router.push("/app")
          }
          return 0
        }
        return prev - 1
      })
    }, 1000)

    return () => clearInterval(interval)
  }, [showSuccess, isLocalMode, router])

  // Waiting for audio separation to complete
  if (waitingForAudio) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center max-w-md">
          <Spinner className="w-8 h-8 mx-auto mb-4" />
          <h2 className="text-lg font-semibold mb-2">{t('separationInProgress')}</h2>
          <p className="text-muted-foreground">
            {t('separationInProgressDesc')}
          </p>
        </div>
      </div>
    )
  }

  // Loading state
  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center">
          <Spinner className="w-8 h-8 mx-auto mb-4" />
          <p className="text-muted-foreground">{t('loadingAnalysis')}</p>
        </div>
      </div>
    )
  }

  // Error state
  if (error) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center max-w-md p-6">
          <p className="text-destructive mb-4">{error}</p>
          {!isLocalMode && (
            <Button variant="outline" asChild>
              <Link href="/app">
                <ArrowLeft className="w-4 h-4 mr-2" />
                {t('backToDashboard')}
              </Link>
            </Button>
          )}
        </div>
      </div>
    )
  }

  // Success screen
  if (showSuccess) {
    const selectionLabels: Record<string, string> = {
      clean: t('options.cleanInstrumental'),
      with_backing: t('options.withBackingVocals'),
      custom: t('options.custom'),
      uploaded: t('options.uploaded'),
      original: t('options.originalAudio'),
    }
    const selectionLabel = selectionLabels[selectedOption] || selectedOption

    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center">
          <h2 className="text-2xl font-semibold text-green-500 mb-4">
            <Check className="w-8 h-8 inline-block mr-2" />
            {t('selectionSubmitted')}
          </h2>
          <p className="mb-2">
            {t('youSelected', { label: selectionLabel })}
          </p>
          <p className="text-muted-foreground">
            {countdown > 0
              ? isLocalMode
                ? t('closingIn', { countdown })
                : t('redirectingIn', { countdown })
              : isLocalMode
                ? t('canCloseWindow')
                : t('redirecting')}
          </p>
        </div>
      </div>
    )
  }

  const amplitudes = waveformData?.amplitudes ?? []

  return (
    <div className="flex flex-col min-h-screen bg-background p-2 md:p-4 gap-2 md:gap-3">
      {/* Header */}
      <header className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between flex-shrink-0">
        <div className="flex items-center gap-3">
          <span className="text-lg font-semibold flex items-center gap-2">
            <img
              src="https://gen.nomadkaraoke.com/nomad-karaoke-logo.svg"
              alt="Nomad Karaoke"
              className="h-8 md:h-10"
              onError={(e) => {
                e.currentTarget.style.display = "none"
              }}
            />
            <span className="sm:hidden">{t('review')}</span>
            <span className="hidden sm:inline">{t('title')}</span>
          </span>
          <span className="text-xs md:text-sm text-muted-foreground truncate">
            {job.artist} {job.artist && job.title ? "-" : ""} {job.title}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={`px-2.5 py-1 rounded-full text-xs ${
              analysisData?.analysis.recommended_selection === "clean"
                ? "bg-green-500/15 text-green-500"
                : "bg-amber-500/15 text-amber-500"
            }`}
          >
            {analysisData?.analysis.recommended_selection === "clean"
              ? t('cleanRecommended')
              : t('reviewBackingVocals')}
          </span>
        </div>
      </header>

      {/* Guidance panel */}
      {analysisData && (
        <InstrumentalGuidancePanel
          analysis={analysisData.analysis}
          audibleSegments={audibleSegments}
        />
      )}

      {/* Waveform Player */}
      <div className="min-h-[200px] md:min-h-[250px] lg:flex-1 lg:min-h-0 flex flex-col bg-card border border-border rounded-xl overflow-hidden">
        {/* Toolbar */}
        <div className="flex items-center justify-between px-2 md:px-4 py-2 md:py-2.5 bg-secondary border-b border-border gap-3 flex-shrink-0 flex-wrap">
          <div className="flex items-center gap-2">
            <Button
              size="icon"
              onClick={togglePlayPause}
              className="rounded-full w-9 h-9 md:w-8 md:h-8"
            >
              {isPlaying ? (
                <Pause className="w-4 h-4" />
              ) : (
                <Play className="w-4 h-4 ml-0.5" />
              )}
            </Button>
            <span className="font-mono text-sm min-w-[90px]">
              {formatTime(currentTime)}
              <span className="text-muted-foreground"> / {formatTime(duration)}</span>
            </span>
          </div>

          <div className="flex items-center gap-1.5">
            <StemComparison
              activeAudio={activeAudio}
              onAudioChange={handleAudioChange}
              hasOriginal={hasOriginal}
              hasWithBacking={hasWithBacking}
              hasCustom={hasCustom}
              hasUploaded={hasUploaded}
              uploadedFilename={uploadedFilename}
              isLoading={isAudioLoading}
            />
          </div>

          <div className="flex items-center gap-3">
            <div className="flex bg-background rounded-md p-0.5">
              {([1, 2, 4] as ZoomLevel[]).map((level) => (
                <button
                  key={level}
                  type="button"
                  onClick={() => setZoomLevel(level)}
                  className={`w-9 h-9 md:w-7 md:h-7 rounded text-xs font-medium transition-colors ${
                    zoomLevel === level
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:bg-secondary hover:text-foreground"
                  }`}
                >
                  {level}x
                </button>
              ))}
            </div>
            <span className="hidden md:inline text-[10px] text-muted-foreground">
              {t('shiftDrag')}
            </span>
            <kbd className="hidden md:inline px-1.5 py-0.5 bg-background border border-border rounded text-[10px] font-mono">
              {t('space')}
            </kbd>
          </div>
        </div>

        {/* Waveform */}
        <div className="relative flex-1 min-h-0">
          <WaveformViewer
            amplitudes={amplitudes}
            duration={duration}
            currentTime={currentTime}
            muteRegions={muteRegions}
            audibleSegments={audibleSegments}
            zoomLevel={zoomLevel}
            onSeek={seekTo}
            onRegionCreate={addRegion}
            isShiftHeld={isShiftHeld}
            highlightedTimeRange={highlightedTimeRange}
          />
          {isAudioLoading && (
            <div className="absolute inset-0 flex items-center justify-center bg-background/50 backdrop-blur-sm">
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Spinner className="w-4 h-4" />
                {t('loadingAudio')}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Hidden audio element */}
      <audio ref={audioRef} src={getAudioUrl()} className="hidden" />

      {/* Bottom section */}
      <div className="flex flex-col lg:flex-row gap-2 md:gap-3 flex-shrink-0">
        {/* Left column: Custom Mix + Alternate Instrumental */}
        <div className="flex flex-col gap-2 md:gap-3 lg:flex-1 lg:min-w-0">
          <MuteRegionEditor
            regions={muteRegions}
            audibleSegments={audibleSegments}
            hasCustom={hasCustom}
            isCreatingCustom={isCreatingCustom}
            onRemoveRegion={removeRegion}
            onClearAll={clearAllRegions}
            onCreateCustom={handleCreateCustom}
            onSeekTo={seekTo}
            onAddSegment={addSegmentAsRegion}
            onHoverRegion={(index) =>
              setHoveredItem(index !== null ? { type: "region", index } : null)
            }
            onHoverSegment={(index) =>
              setHoveredItem(index !== null ? { type: "segment", index } : null)
            }
          />

          {/* Alternate Instrumental section */}
          <div className="bg-card border border-border rounded-lg p-3">
            <div className="flex items-center justify-between">
              <span className="text-sm font-semibold">{t('alternateInstrumental')}</span>
              <CustomUpload onUpload={handleUpload} isUploading={isUploading} />
            </div>
            <p className="text-xs text-muted-foreground mt-1.5">
              {t('uploadYourOwn', { duration: formatTime(duration) })}
            </p>
            {hasUploaded && uploadedFilename && (
              <p className="text-xs text-green-500 mt-1">
                ✓ {t('usingFile', { filename: uploadedFilename })}
              </p>
            )}
          </div>
        </div>

        {/* Right column: Selection panel */}
        <div className="w-full lg:w-[340px] lg:flex-shrink-0 bg-card border border-border rounded-lg p-3 flex flex-col gap-2">
          <SelectionOptions
            value={selectedOption}
            onChange={setSelectedOption}
            hasWithBacking={hasWithBacking}
            hasOriginal={hasOriginal}
            hasCustom={hasCustom}
            hasUploaded={hasUploaded}
            uploadedFilename={uploadedFilename}
            muteRegionsCount={muteRegions.length}
            backingVocalAnalysis={analysisData?.analysis}
          />
          <Button
            id="submit-btn"
            onClick={handleSubmit}
            disabled={isSubmitting}
            size="lg"
            className="mt-auto w-full min-h-[44px] text-base font-semibold"
          >
            <Check className="w-5 h-5 mr-2" />
            {isSubmitting ? t('submitting') : t('confirmContinue')}
          </Button>
        </div>
      </div>
    </div>
  )
}

// Helper function to merge overlapping regions
function mergeOverlappingRegions(regions: MuteRegion[]): MuteRegion[] {
  if (regions.length < 2) return regions

  const merged: MuteRegion[] = [regions[0]]
  for (let i = 1; i < regions.length; i++) {
    const last = merged[merged.length - 1]
    const curr = regions[i]

    if (curr.start_seconds <= last.end_seconds + 0.5) {
      last.end_seconds = Math.max(last.end_seconds, curr.end_seconds)
    } else {
      merged.push(curr)
    }
  }
  return merged
}
