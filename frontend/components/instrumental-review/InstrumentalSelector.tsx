"use client"

import { useState, useEffect, useRef, useCallback, useMemo } from "react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import { Spinner } from "@/components/ui/spinner"
import { ArrowLeft, Play, Pause, Check } from "lucide-react"
import Link from "next/link"
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
  const [isCreatingCustom, setIsCreatingCustom] = useState(false)
  const [isUploading, setIsUploading] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [isShiftHeld, setIsShiftHeld] = useState(false)

  // Derived state
  const hasOriginal = analysisData?.has_original ?? false
  const hasWithBacking = !!analysisData?.audio_urls.with_backing
  const audibleSegments = useMemo(
    () => analysisData?.analysis.audible_segments ?? [],
    [analysisData?.analysis.audible_segments]
  )

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

  // Fetch data on mount
  useEffect(() => {
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
  }, [job.job_id, isLocalMode])

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
  }, [job.job_id, activeAudio, isLocalMode, analysisData])

  // Handle audio type change
  const handleAudioChange = useCallback(
    (type: AudioType) => {
      const audio = audioRef.current
      if (!audio) return

      const wasPlaying = !audio.paused
      const time = audio.currentTime

      audio.pause()
      setActiveAudio(type)

      // Update audio source
      // For cloud mode, use signed URLs from analysis data
      let audioUrl: string
      if (!isLocalMode && analysisData?.audio_urls) {
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
    },
    [job.job_id, isLocalMode, analysisData]
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
      await api.createCustomInstrumental(job.job_id, muteRegions)
      setHasCustom(true)
      setSelectedOption("custom")
      setActiveAudio("custom")

      // Update audio source
      if (audio) {
        audio.src = api.getAudioStreamUrl(job.job_id, "custom_instrumental")
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
      const correctionData = await lyricsReviewApi.getCorrectionData(job.job_id)
      await lyricsReviewApi.completeReview(job.job_id, correctionData, selectedOption)

      if (isLocalMode) {
        // In local mode, show success screen with countdown then close
        setShowSuccess(true)
        setCountdown(2)
      } else {
        // Redirect to dashboard after short delay (cloud mode)
        toast.success("Review completed successfully")
        setTimeout(() => {
          router.push("/app")
        }, 1500)
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to complete review")
      setIsSubmitting(false)
    }
  }, [job.job_id, selectedOption, router, isLocalMode])

  // Countdown effect for success screen
  useEffect(() => {
    if (!showSuccess) return

    const interval = setInterval(() => {
      setCountdown((prev) => {
        if (prev <= 1) {
          clearInterval(interval)
          window.close()
          return 0
        }
        return prev - 1
      })
    }, 1000)

    return () => clearInterval(interval)
  }, [showSuccess])

  // Loading state
  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center">
          <Spinner className="w-8 h-8 mx-auto mb-4" />
          <p className="text-muted-foreground">Loading instrumental analysis...</p>
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
                Back to dashboard
              </Link>
            </Button>
          )}
        </div>
      </div>
    )
  }

  // Success screen (local mode)
  if (showSuccess) {
    const selectionLabels: Record<string, string> = {
      clean: "Clean Instrumental",
      with_backing: "With Backing Vocals",
      custom: "Custom",
      uploaded: "Uploaded Instrumental",
      original: "Original Audio",
    }
    const selectionLabel = selectionLabels[selectedOption] || selectedOption

    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center">
          <h2 className="text-2xl font-semibold text-green-500 mb-4">
            <Check className="w-8 h-8 inline-block mr-2" />
            Selection Submitted
          </h2>
          <p className="mb-2">
            You selected: <strong>{selectionLabel}</strong>
          </p>
          <p className="text-muted-foreground">
            {countdown > 0
              ? `Closing in ${countdown}s...`
              : "You can close this window now."}
          </p>
        </div>
      </div>
    )
  }

  const amplitudes = waveformData?.amplitudes ?? []

  return (
    <div className="flex flex-col h-screen bg-background p-4 gap-3 overflow-hidden">
      {/* Header */}
      <header className="flex items-center justify-between flex-shrink-0">
        <div className="flex items-center gap-3">
          <span className="text-lg font-semibold flex items-center gap-2">
            <img
              src="https://gen.nomadkaraoke.com/nomad-karaoke-logo.svg"
              alt="Nomad Karaoke"
              className="h-10"
              onError={(e) => {
                e.currentTarget.style.display = "none"
              }}
            />
            Instrumental Review
          </span>
          <span className="text-sm text-muted-foreground">
            {job.artist} {job.artist && job.title ? "-" : ""} {job.title}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {audibleSegments.length > 0 && (
            <>
              <span className="px-2.5 py-1 rounded-full text-xs bg-secondary text-muted-foreground">
                {audibleSegments.length} segments
              </span>
              <span className="px-2.5 py-1 rounded-full text-xs bg-secondary text-muted-foreground">
                {analysisData?.analysis.audible_percentage.toFixed(0)}% backing vocals
              </span>
            </>
          )}
          <span
            className={`px-2.5 py-1 rounded-full text-xs ${
              analysisData?.analysis.recommended_selection === "clean"
                ? "bg-green-500/15 text-green-500"
                : "bg-amber-500/15 text-amber-500"
            }`}
          >
            {analysisData?.analysis.recommended_selection === "clean"
              ? "Clean recommended"
              : "Review needed"}
          </span>
        </div>
      </header>

      {/* Waveform Player */}
      <div className="flex-1 flex flex-col bg-card border border-border rounded-xl overflow-hidden min-h-0">
        {/* Toolbar */}
        <div className="flex items-center justify-between px-4 py-2.5 bg-secondary border-b border-border gap-3 flex-shrink-0 flex-wrap">
          <div className="flex items-center gap-2">
            <Button
              size="icon"
              onClick={togglePlayPause}
              className="rounded-full w-8 h-8"
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
            />
            <CustomUpload onUpload={handleUpload} isUploading={isUploading} />
          </div>

          <div className="flex items-center gap-3">
            <div className="flex bg-background rounded-md p-0.5">
              {([1, 2, 4] as ZoomLevel[]).map((level) => (
                <button
                  key={level}
                  type="button"
                  onClick={() => setZoomLevel(level)}
                  className={`w-7 h-7 rounded text-xs font-medium transition-colors ${
                    zoomLevel === level
                      ? "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:bg-secondary hover:text-foreground"
                  }`}
                >
                  {level}x
                </button>
              ))}
            </div>
            <span className="text-[10px] text-muted-foreground">
              <kbd className="px-1.5 py-0.5 bg-background border border-border rounded text-[10px] font-mono">
                Shift
              </kbd>
              +drag
            </span>
            <kbd className="px-1.5 py-0.5 bg-background border border-border rounded text-[10px] font-mono">
              Space
            </kbd>
          </div>
        </div>

        {/* Waveform */}
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
        />

        {/* Time axis */}
        <div className="flex justify-between px-3 py-1 text-[10px] text-muted-foreground bg-black/40 flex-shrink-0">
          <span>0:00</span>
          <span>{formatTime(duration * 0.25)}</span>
          <span>{formatTime(duration * 0.5)}</span>
          <span>{formatTime(duration * 0.75)}</span>
          <span>{formatTime(duration)}</span>
        </div>
      </div>

      {/* Hidden audio element */}
      <audio ref={audioRef} src={getAudioUrl()} className="hidden" />

      {/* Bottom section */}
      <div className="flex gap-3 flex-shrink-0">
        {/* Mute regions panel */}
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
        />

        {/* Selection panel */}
        <div className="w-[340px] bg-card border border-border rounded-lg p-3 flex flex-col gap-2">
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
            className="mt-auto w-full"
          >
            <Check className="w-4 h-4 mr-2" />
            {isSubmitting ? "Submitting..." : "Confirm & Continue"}
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
