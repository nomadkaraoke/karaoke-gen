"use client"

import { useState, useEffect, useRef, useCallback } from "react"
import { useTranslations } from 'next-intl'
import { api, Job, AudioEditInfo, AudioEditResponse, AudioEditEntry, AudioEditSessionMeta } from "@/lib/api"
import { useAudioEditAutoSave } from "@/hooks/use-audio-edit-autosave"
import { AudioEditRestoreDialog } from "./AudioEditRestoreDialog"
import { Spinner } from "@/components/ui/spinner"
import { Button } from "@/components/ui/button"
import { ThemeToggle } from "@/components/ThemeToggle"
import {
  ArrowLeft,
  AlertCircle,
  Undo2,
  Redo2,
  Scissors,
  Play,
  Pause,
  SkipBack,
  SkipForward,
  Volume2,
  VolumeX,
  Upload,
  Check,
  ChevronDown,
  ChevronRight,
  X,
} from "lucide-react"
import Link from "next/link"

// ─── Types ─────────────────────────────────────────────────────────

type EditMode = "select" | "trim"
type AudioTab = "original" | "edited"

interface Selection {
  startSeconds: number
  endSeconds: number
}

// ─── Helpers ───────────────────────────────────────────────────────

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds)) return "0:00"
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins}:${secs.toString().padStart(2, "0")}`
}

function formatTimePrecise(seconds: number): string {
  if (!Number.isFinite(seconds)) return "0:00.0"
  const sign = seconds < 0 ? "-" : ""
  const abs = Math.abs(seconds)
  const mins = Math.floor(abs / 60)
  const secs = (abs % 60).toFixed(1)
  return `${sign}${mins}:${secs.padStart(4, "0")}`
}

const RULER_HEIGHT = 20

const NICE_INTERVALS = [1, 2, 5, 10, 15, 30, 60]

function getTickInterval(duration: number, canvasWidth: number) {
  const targetPx = 120
  const secondsPerPixel = duration / canvasWidth
  const targetInterval = secondsPerPixel * targetPx

  let major = NICE_INTERVALS[NICE_INTERVALS.length - 1]
  for (const interval of NICE_INTERVALS) {
    if (interval >= targetInterval) {
      major = interval
      break
    }
  }

  let minor = major / 4
  if (major >= 60) minor = 15
  else if (major >= 30) minor = 10
  else if (major >= 10) minor = 5
  else if (major >= 5) minor = 1
  else if (major >= 2) minor = 0.5
  else minor = 0.25

  return { major, minor }
}

// ─── WaveformCanvas ────────────────────────────────────────────────

function WaveformCanvas({
  amplitudes,
  duration,
  currentTime,
  selection,
  onSeek,
  onSelectionChange,
  onSelectionComplete,
}: {
  amplitudes: number[]
  duration: number
  currentTime: number
  selection: Selection | null
  onSeek: (time: number) => void
  onSelectionChange: (sel: Selection | null) => void
  onSelectionComplete: (sel: Selection) => void
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const isDraggingRef = useRef(false)
  const dragStartTimeRef = useRef(0)

  const drawWaveform = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas || !amplitudes.length) return

    const ctx = canvas.getContext("2d")
    if (!ctx) return

    const width = canvas.width
    const height = canvas.height
    const waveformHeight = height - RULER_HEIGHT
    const centerY = waveformHeight / 2

    // Clear
    ctx.fillStyle = "#0d1117"
    ctx.fillRect(0, 0, width, height)

    // Center line
    ctx.strokeStyle = "rgba(255, 255, 255, 0.1)"
    ctx.setLineDash([4, 4])
    ctx.beginPath()
    ctx.moveTo(0, centerY)
    ctx.lineTo(width, centerY)
    ctx.stroke()
    ctx.setLineDash([])

    // Waveform bars
    const barWidth = width / amplitudes.length
    amplitudes.forEach((amp, i) => {
      const x = i * barWidth
      const barHeight = Math.max(2, amp * waveformHeight * 0.9)
      const y = centerY - barHeight / 2
      ctx.fillStyle = "#60a5fa"
      ctx.fillRect(x, y, Math.max(1, barWidth - 0.5), barHeight)
    })

    // Selection overlay
    if (selection && duration > 0) {
      const startX = (selection.startSeconds / duration) * width
      const endX = (selection.endSeconds / duration) * width

      ctx.fillStyle = "rgba(250, 204, 21, 0.15)"
      ctx.fillRect(startX, 0, endX - startX, waveformHeight)

      ctx.strokeStyle = "rgba(250, 204, 21, 0.7)"
      ctx.lineWidth = 1.5
      ctx.setLineDash([])
      ctx.beginPath()
      ctx.moveTo(startX, 0)
      ctx.lineTo(startX, waveformHeight)
      ctx.moveTo(endX, 0)
      ctx.lineTo(endX, waveformHeight)
      ctx.stroke()
    }

    // Time ruler
    const rulerY = waveformHeight
    ctx.fillStyle = "rgba(0, 0, 0, 0.5)"
    ctx.fillRect(0, rulerY, width, RULER_HEIGHT)

    ctx.strokeStyle = "rgba(255, 255, 255, 0.15)"
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(0, rulerY)
    ctx.lineTo(width, rulerY)
    ctx.stroke()

    if (duration > 0) {
      const { major, minor } = getTickInterval(duration, width)

      ctx.strokeStyle = "rgba(255, 255, 255, 0.15)"
      ctx.lineWidth = 1
      for (let t = 0; t <= duration; t += minor) {
        const x = (t / duration) * width
        ctx.beginPath()
        ctx.moveTo(x, rulerY)
        ctx.lineTo(x, rulerY + 4)
        ctx.stroke()
      }

      ctx.strokeStyle = "rgba(255, 255, 255, 0.4)"
      ctx.fillStyle = "rgba(255, 255, 255, 0.6)"
      ctx.font = "10px monospace"
      ctx.textBaseline = "bottom"
      for (let t = 0; t <= duration; t += major) {
        const x = (t / duration) * width
        ctx.beginPath()
        ctx.moveTo(x, rulerY)
        ctx.lineTo(x, rulerY + 8)
        ctx.stroke()

        const label = formatTime(t)
        const textWidth = ctx.measureText(label).width
        const labelX = Math.min(x + 3, width - textWidth - 2)
        ctx.fillText(label, Math.max(2, labelX), rulerY + RULER_HEIGHT - 2)
      }
    }
  }, [amplitudes, duration, selection])

  const resizeCanvas = useCallback(() => {
    const canvas = canvasRef.current
    const container = containerRef.current
    if (!canvas || !container) return

    canvas.width = container.clientWidth
    canvas.height = container.clientHeight
  }, [])

  useEffect(() => {
    resizeCanvas()
    drawWaveform()
  }, [resizeCanvas, drawWaveform])

  useEffect(() => {
    const handleResize = () => {
      resizeCanvas()
      drawWaveform()
    }
    window.addEventListener("resize", handleResize)
    return () => window.removeEventListener("resize", handleResize)
  }, [resizeCanvas, drawWaveform])

  const getTimeFromEvent = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current
    if (!canvas || !duration) return 0
    const rect = canvas.getBoundingClientRect()
    const x = e.clientX - rect.left
    return Math.max(0, Math.min(duration, (x / rect.width) * duration))
  }

  const handleMouseDown = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!duration) return
    const time = getTimeFromEvent(e)
    isDraggingRef.current = true
    dragStartTimeRef.current = time
    onSelectionChange(null)
  }

  const handleMouseMove = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!isDraggingRef.current) return
    const time = getTimeFromEvent(e)
    const start = Math.min(dragStartTimeRef.current, time)
    const end = Math.max(dragStartTimeRef.current, time)
    if (end - start > 0.3) {
      onSelectionChange({ startSeconds: start, endSeconds: end })
    }
  }

  const handleMouseUp = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!isDraggingRef.current) return
    isDraggingRef.current = false

    const time = getTimeFromEvent(e)
    const start = Math.min(dragStartTimeRef.current, time)
    const end = Math.max(dragStartTimeRef.current, time)

    if (end - start > 0.5) {
      const sel = { startSeconds: start, endSeconds: end }
      onSelectionChange(sel)
      onSelectionComplete(sel)
    } else {
      // Click to seek
      onSelectionChange(null)
      onSeek(time)
    }
  }

  const handleMouseLeave = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (isDraggingRef.current) {
      handleMouseUp(e)
    }
  }

  // Playhead position
  const playheadPosition =
    duration > 0 && canvasRef.current
      ? (currentTime / duration) * canvasRef.current.width
      : 0

  return (
    <div
      ref={containerRef}
      className="relative flex-1 overflow-x-auto overflow-y-hidden bg-[#0d1117] min-h-[140px] rounded-lg"
    >
      <div className="relative h-full" style={{ minWidth: "100%" }}>
        <canvas
          ref={canvasRef}
          className="block h-full cursor-crosshair"
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
          onMouseLeave={handleMouseLeave}
        />
        {/* Playhead */}
        <div
          className="absolute top-0 w-0.5 h-full pointer-events-none z-10"
          style={{
            left: `${playheadPosition}px`,
            backgroundColor: "var(--primary, #f472b6)",
            boxShadow: "0 0 8px var(--primary, #f472b6)",
          }}
        >
          <div
            className="absolute -top-0 -left-1 w-2.5 h-2.5 rounded-full"
            style={{ backgroundColor: "var(--primary, #f472b6)" }}
          />
        </div>
      </div>
    </div>
  )
}

// ─── Main AudioEditor Component ────────────────────────────────────

interface AudioEditorProps {
  job: Job
}

export function AudioEditor({ job }: AudioEditorProps) {
  const t = useTranslations('audioEditor')
  const tc = useTranslations('common')
  // Loading state
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Audio info from backend
  const [audioInfo, setAudioInfo] = useState<AudioEditInfo | null>(null)

  // Edit state
  const [editStack, setEditStack] = useState<AudioEditEntry[]>([])
  const [canUndo, setCanUndo] = useState(false)
  const [canRedo, setCanRedo] = useState(false)

  // UI state
  const [activeTab, setActiveTab] = useState<AudioTab>("edited")
  const [selection, setSelection] = useState<Selection | null>(null)
  const [isOperating, setIsOperating] = useState(false)
  const [operationError, setOperationError] = useState<string | null>(null)
  const [showHistory, setShowHistory] = useState(false)
  const [showGuidance, setShowGuidance] = useState(() => {
    if (typeof window === "undefined") return true
    return localStorage.getItem("audio-editor-guidance-dismissed") !== "true"
  })

  // Audio playback
  const audioRef = useRef<HTMLAudioElement>(null)
  const [isPlaying, setIsPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [audioDuration, setAudioDuration] = useState(0)
  const [isAudioLoading, setIsAudioLoading] = useState(false)

  // Submit state
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [showSubmitConfirm, setShowSubmitConfirm] = useState(false)
  const [submitSuccess, setSubmitSuccess] = useState(false)
  const [redirectCountdown, setRedirectCountdown] = useState(3)

  // Session restore state
  const [showRestoreDialog, setShowRestoreDialog] = useState(false)
  const [savedSessions, setSavedSessions] = useState<AudioEditSessionMeta[]>([])
  const [isRestoringSession, setIsRestoringSession] = useState(false)

  // Current waveform and audio URL
  const currentAmplitudes = activeTab === "original"
    ? (audioInfo?.original_waveform_data?.amplitudes ?? [])
    : (audioInfo?.waveform_data?.amplitudes ?? [])

  const currentAudioUrl = activeTab === "original"
    ? audioInfo?.original_audio_url ?? ""
    : audioInfo?.current_audio_url ?? ""

  const currentDuration = activeTab === "original"
    ? (audioInfo?.original_duration_seconds ?? 0)
    : (audioInfo?.current_duration_seconds ?? 0)

  // ─── Auto-save ─────────────────────────────────────────────────

  const { saveSession } = useAudioEditAutoSave({
    jobId: job.job_id,
    editStack,
    originalDuration: audioInfo?.original_duration_seconds ?? 0,
    currentDuration: audioInfo?.current_duration_seconds ?? 0,
  })

  // ─── Load initial data ─────────────────────────────────────────

  useEffect(() => {
    async function loadData() {
      try {
        setIsLoading(true)
        setError(null)

        // Load audio info and check for saved sessions in parallel
        const [info, sessionsResult] = await Promise.all([
          api.getInputAudioInfo(job.job_id),
          api.listAudioEditSessions(job.job_id).catch(() => ({ sessions: [] })),
        ])

        setAudioInfo(info)
        setEditStack(info.edit_stack || [])
        setCanUndo(info.can_undo)
        setCanRedo(info.can_redo)

        // If no edits applied yet but saved sessions exist, offer restore
        if ((info.edit_stack || []).length === 0 && sessionsResult.sessions.length > 0) {
          setSavedSessions(sessionsResult.sessions)
          setShowRestoreDialog(true)
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load audio data")
      } finally {
        setIsLoading(false)
      }
    }
    loadData()
  }, [job.job_id])

  // ─── Audio playback ────────────────────────────────────────────

  useEffect(() => {
    const audio = audioRef.current
    if (!audio || !currentAudioUrl) return

    const wasPlaying = isPlaying
    audio.pause()
    setIsAudioLoading(true)

    audio.src = currentAudioUrl
    audio.load()

    const handleLoaded = () => {
      setIsAudioLoading(false)
      setAudioDuration(audio.duration)
      if (wasPlaying) {
        audio.play().catch(() => {})
      }
    }
    const handleError = () => {
      setIsAudioLoading(false)
    }

    audio.addEventListener("loadeddata", handleLoaded, { once: true })
    audio.addEventListener("error", handleError, { once: true })

    return () => {
      audio.removeEventListener("loadeddata", handleLoaded)
      audio.removeEventListener("error", handleError)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentAudioUrl])

  // Playhead animation
  useEffect(() => {
    const audio = audioRef.current
    if (!audio || !isPlaying) return

    let animationId: number
    const updateTime = () => {
      setCurrentTime(audio.currentTime)
      animationId = requestAnimationFrame(updateTime)
    }
    animationId = requestAnimationFrame(updateTime)
    return () => cancelAnimationFrame(animationId)
  }, [isPlaying])

  const togglePlay = useCallback(() => {
    const audio = audioRef.current
    if (!audio) return

    if (audio.paused) {
      audio.play().then(() => setIsPlaying(true)).catch(() => {})
    } else {
      audio.pause()
      setIsPlaying(false)
    }
  }, [])

  const seekTo = useCallback((time: number) => {
    const audio = audioRef.current
    if (!audio) return
    audio.currentTime = Math.max(0, Math.min(time, audio.duration || currentDuration))
    setCurrentTime(audio.currentTime)
  }, [currentDuration])

  const skipForward = useCallback(() => seekTo(currentTime + 5), [seekTo, currentTime])
  const skipBack = useCallback(() => seekTo(currentTime - 5), [seekTo, currentTime])

  // ─── Keyboard shortcuts ────────────────────────────────────────

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Don't capture if user is typing in an input
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return

      if (e.key === " ") {
        e.preventDefault()
        togglePlay()
      } else if (e.key === "z" && (e.metaKey || e.ctrlKey) && e.shiftKey) {
        e.preventDefault()
        handleRedo()
      } else if (e.key === "z" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault()
        handleUndo()
      } else if (e.key === "Delete" || e.key === "Backspace") {
        if (selection) {
          e.preventDefault()
          handleCut()
        }
      } else if (e.key === "m" || e.key === "M") {
        if (selection) {
          e.preventDefault()
          handleMute()
        }
      }
    }

    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selection, canUndo, canRedo])

  // ─── Edit operations ──────────────────────────────────────────

  function updateFromResponse(resp: AudioEditResponse) {
    setAudioInfo((prev) => {
      if (!prev) return prev
      return {
        ...prev,
        current_duration_seconds: resp.duration_after,
        current_audio_url: resp.current_audio_url,
        waveform_data: resp.waveform_data,
      }
    })
    setEditStack(resp.edit_stack)
    setCanUndo(resp.can_undo)
    setCanRedo(resp.can_redo)
    setActiveTab("edited")
    setSelection(null)
  }

  async function handleApply(operation: string, params: Record<string, unknown>) {
    setIsOperating(true)
    setOperationError(null)
    try {
      const resp = await api.applyAudioEdit(job.job_id, operation, params)
      updateFromResponse(resp)
    } catch (err) {
      setOperationError(err instanceof Error ? err.message : t('editFailed'))
    } finally {
      setIsOperating(false)
    }
  }

  async function handleUndo() {
    if (!canUndo || isOperating) return
    setIsOperating(true)
    setOperationError(null)
    try {
      const resp = await api.undoAudioEdit(job.job_id)
      updateFromResponse(resp)
    } catch (err) {
      setOperationError(err instanceof Error ? err.message : t('undoFailed'))
    } finally {
      setIsOperating(false)
    }
  }

  async function handleRedo() {
    if (!canRedo || isOperating) return
    setIsOperating(true)
    setOperationError(null)
    try {
      const resp = await api.redoAudioEdit(job.job_id)
      updateFromResponse(resp)
    } catch (err) {
      setOperationError(err instanceof Error ? err.message : t('redoFailed'))
    } finally {
      setIsOperating(false)
    }
  }

  function handleTrimStart() {
    if (!selection) return
    handleApply("trim_start", { end_seconds: selection.endSeconds })
  }

  function handleTrimEnd() {
    if (!selection) return
    handleApply("trim_end", { start_seconds: selection.startSeconds })
  }

  function handleCut() {
    if (!selection) return
    handleApply("cut", {
      start_seconds: selection.startSeconds,
      end_seconds: selection.endSeconds,
    })
  }

  function handleMute() {
    if (!selection) return
    handleApply("mute", {
      start_seconds: selection.startSeconds,
      end_seconds: selection.endSeconds,
    })
  }

  // ─── Join (upload) ─────────────────────────────────────────────

  const [showJoinDialog, setShowJoinDialog] = useState(false)
  const [joinFile, setJoinFile] = useState<File | null>(null)
  const [isUploading, setIsUploading] = useState(false)

  async function handleJoin(position: "start" | "end") {
    if (!joinFile) return
    setIsUploading(true)
    setOperationError(null)
    try {
      const { upload_id } = await api.uploadAudioForJoin(job.job_id, joinFile)
      const operation = position === "start" ? "join_start" : "join_end"
      const resp = await api.applyAudioEdit(job.job_id, operation, { upload_id })
      updateFromResponse(resp)
      setShowJoinDialog(false)
      setJoinFile(null)
    } catch (err) {
      setOperationError(err instanceof Error ? err.message : t('editFailed'))
    } finally {
      setIsUploading(false)
    }
  }

  // ─── Session restore ────────────────────────────────────────────

  async function handleRestoreSession(entries: AudioEditEntry[]) {
    setIsRestoringSession(true)
    setShowRestoreDialog(false)
    setOperationError(null)

    try {
      // Replay each edit sequentially
      for (const entry of entries) {
        const resp = await api.applyAudioEdit(job.job_id, entry.operation, entry.params as Record<string, unknown>)
        updateFromResponse(resp)
      }
    } catch (err) {
      setOperationError(err instanceof Error ? err.message : t('restoreFailed'))
    } finally {
      setIsRestoringSession(false)
    }
  }

  // ─── Submit ────────────────────────────────────────────────────

  async function handleSubmit() {
    // Save session before submit
    if (editStack.length > 0) {
      await saveSession("submit")
    }
    setIsSubmitting(true)
    try {
      await api.submitAudioEdit(job.job_id, editStack.length > 0 ? editStack : undefined)
      setSubmitSuccess(true)
      setShowSubmitConfirm(false)
    } catch (err) {
      setOperationError(err instanceof Error ? err.message : t('submitFailed'))
    } finally {
      setIsSubmitting(false)
    }
  }

  // Redirect countdown after submit
  useEffect(() => {
    if (!submitSuccess) return
    const timer = setInterval(() => {
      setRedirectCountdown((c) => {
        if (c <= 1) {
          window.location.href = "/app"
          return 0
        }
        return c - 1
      })
    }, 1000)
    return () => clearInterval(timer)
  }, [submitSuccess])

  // Handle audio ended
  useEffect(() => {
    const audio = audioRef.current
    if (!audio) return
    const handleEnded = () => setIsPlaying(false)
    audio.addEventListener("ended", handleEnded)
    return () => audio.removeEventListener("ended", handleEnded)
  }, [])

  // ─── Render ────────────────────────────────────────────────────

  if (isLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center">
          <Spinner className="w-8 h-8 mx-auto mb-4" />
          <p className="text-muted-foreground">{t('loading')}</p>
        </div>
      </div>
    )
  }

  if (error || !audioInfo) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center max-w-md p-6">
          <AlertCircle className="w-12 h-12 mx-auto mb-4 text-red-500" />
          <h1 className="text-xl font-semibold mb-2">{t('failedToLoad')}</h1>
          <p className="text-muted-foreground mb-4">{error || t('couldNotLoadData')}</p>
          <Button variant="outline" asChild>
            <Link href="/app">
              <ArrowLeft className="w-4 h-4 mr-2" />
              {t('backToDashboard')}
            </Link>
          </Button>
        </div>
      </div>
    )
  }

  if (submitSuccess) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <div className="text-center max-w-md p-6 space-y-4">
          <div
            className="w-16 h-16 rounded-full flex items-center justify-center mx-auto"
            style={{ backgroundColor: "rgba(34, 197, 94, 0.15)" }}
          >
            <Check className="w-8 h-8 text-green-500" />
          </div>
          <h1 className="text-xl font-semibold">{t('editComplete')}</h1>
          <p className="text-muted-foreground">
            {t('editsWillBeReviewed')}
          </p>
          <p className="text-sm text-muted-foreground">
            {t('redirectingIn', { count: redirectCountdown })}
          </p>
          <Button variant="outline" asChild>
            <Link href="/app">{t('backToDashboard')}</Link>
          </Button>
        </div>
      </div>
    )
  }

  const hasEdits = editStack.length > 0
  const durationChange = hasEdits
    ? (audioInfo.current_duration_seconds - audioInfo.original_duration_seconds)
    : 0

  return (
    <div className="flex flex-col min-h-screen bg-background">
      {/* Header */}
      <header className="border-b bg-card/80 backdrop-blur-sm sticky top-0 z-50 px-4 py-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Button variant="ghost" size="sm" asChild>
              <Link href="/app">
                <ArrowLeft className="w-4 h-4 mr-2" />
                Back
              </Link>
            </Button>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="/nomad-karaoke-logo.svg" alt="Nomad Karaoke" style={{ height: 40 }} />
            <div>
              <h1 className="text-lg font-bold">Audio Editor</h1>
              <p className="text-xs text-muted-foreground">
                {audioInfo.artist || job.artist} - {audioInfo.title || job.title}
              </p>
            </div>
          </div>
          <ThemeToggle />
        </div>
      </header>

      {/* Hidden audio element */}
      <audio ref={audioRef} preload="auto" />

      <main className="flex-1 p-2 md:p-4 space-y-3">
        {/* Guidance panel */}
        {showGuidance && (
          <div
            className="rounded-lg border p-3 text-sm relative"
            style={{ borderColor: "var(--card-border)", backgroundColor: "var(--card)" }}
          >
            <button
              onClick={() => {
                setShowGuidance(false)
                localStorage.setItem("audio-editor-guidance-dismissed", "true")
              }}
              className="absolute top-2 right-2 text-muted-foreground hover:text-foreground"
            >
              <X className="w-4 h-4" />
            </button>
            <div className="flex items-start gap-2">
              <Scissors className="w-4 h-4 mt-0.5 shrink-0 text-blue-400" />
              <div className="space-y-1.5">
                <p className="font-medium" style={{ color: "var(--text)" }}>{t('keyboardShortcuts')}</p>
                <ul className="space-y-1 text-xs text-muted-foreground">
                  <li>{t('shortcutSpace')}</li>
                  <li>{t('shortcutUndo')}</li>
                  <li>{t('shortcutRedo')}</li>
                  <li>{t('shortcutDelete')}</li>
                  <li>{t('shortcutMute')}</li>
                </ul>
              </div>
            </div>
          </div>
        )}

        {/* Operation error */}
        {operationError && (
          <div className="text-sm text-red-400 bg-red-500/10 rounded p-2">
            {operationError}
            <button onClick={() => setOperationError(null)} className="ml-2 underline text-xs">dismiss</button>
          </div>
        )}

        {/* Toolbar */}
        <div
          className="rounded-lg border p-2 flex flex-wrap items-center gap-2"
          style={{ borderColor: "var(--card-border)", backgroundColor: "var(--card)" }}
        >
          {/* Undo/Redo */}
          <div className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="sm"
              disabled={!canUndo || isOperating}
              onClick={handleUndo}
              title={t('undo')}
            >
              <Undo2 className="w-4 h-4" />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              disabled={!canRedo || isOperating}
              onClick={handleRedo}
              title={t('redo')}
            >
              <Redo2 className="w-4 h-4" />
            </Button>
          </div>

          <div className="w-px h-6" style={{ backgroundColor: "var(--card-border)" }} />

          {/* Edit actions (only when selection exists) */}
          {selection ? (
            <div className="flex items-center gap-1">
              <span className="text-xs text-muted-foreground mr-1">
                {formatTimePrecise(selection.startSeconds)} - {formatTimePrecise(selection.endSeconds)}
                ({formatTimePrecise(selection.endSeconds - selection.startSeconds)})
              </span>
              {selection.startSeconds < 1 && (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={isOperating}
                  onClick={handleTrimStart}
                  className="text-xs h-7"
                >
                  {t('trimStart')}
                </Button>
              )}
              {selection.endSeconds > currentDuration - 1 && (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={isOperating}
                  onClick={handleTrimEnd}
                  className="text-xs h-7"
                >
                  {t('trimEnd')}
                </Button>
              )}
              <Button
                variant="outline"
                size="sm"
                disabled={isOperating}
                onClick={handleCut}
                className="text-xs h-7"
                title={t('cutSelection')}
              >
                <Scissors className="w-3 h-3 mr-1" />
                Cut
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={isOperating}
                onClick={handleMute}
                className="text-xs h-7"
                title={t('muteSelection')}
              >
                <VolumeX className="w-3 h-3 mr-1" />
                Mute
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setSelection(null)}
                className="text-xs h-7"
              >
                Clear
              </Button>
            </div>
          ) : (
            <span className="text-xs text-muted-foreground">
              Drag on the waveform to select a region
            </span>
          )}

          <div className="flex-1" />

          {/* Join upload */}
          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowJoinDialog(true)}
            disabled={isOperating}
            className="text-xs h-7"
          >
            <Upload className="w-3 h-3 mr-1" />
            Join
          </Button>

          {/* Save progress */}
          {hasEdits && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => saveSession("manual")}
              disabled={isOperating}
              className="text-xs h-7"
              title="Save progress"
            >
              Save
            </Button>
          )}

          <div className="w-px h-6" style={{ backgroundColor: "var(--card-border)" }} />

          {/* Submit */}
          <Button
            size="sm"
            disabled={isOperating || isSubmitting}
            onClick={() => {
              if (hasEdits) {
                setShowSubmitConfirm(true)
              } else {
                handleSubmit()
              }
            }}
            className="text-xs h-7 bg-green-600 hover:bg-green-700 text-white"
          >
            <Check className="w-3 h-3 mr-1" />
            {hasEdits ? t('submitForReview') : t('continueWithoutEditing')}
          </Button>
        </div>

        {/* Tab switcher */}
        <div className="flex items-center gap-1">
          <button
            onClick={() => setActiveTab("original")}
            className={`px-3 py-1.5 rounded text-xs font-medium transition-all ${
              activeTab === "original"
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            {t('original')} ({formatTime(audioInfo.original_duration_seconds)})
          </button>
          <button
            onClick={() => setActiveTab("edited")}
            className={`px-3 py-1.5 rounded text-xs font-medium transition-all ${
              activeTab === "edited"
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            {t('edited')} ({formatTime(audioInfo.current_duration_seconds)})
            {hasEdits && durationChange !== 0 && (
              <span className={`ml-1 ${durationChange < 0 ? "text-green-400" : "text-amber-400"}`}>
                ({durationChange > 0 ? "+" : ""}{formatTimePrecise(durationChange)})
              </span>
            )}
          </button>
          {isAudioLoading && <Spinner className="w-4 h-4 ml-2" />}
        </div>

        {/* Waveform */}
        <WaveformCanvas
          amplitudes={currentAmplitudes}
          duration={currentDuration}
          currentTime={currentTime}
          selection={activeTab === "edited" ? selection : null}
          onSeek={seekTo}
          onSelectionChange={activeTab === "edited" ? setSelection : () => {}}
          onSelectionComplete={activeTab === "edited" ? setSelection : () => {}}
        />

        {/* Playback controls */}
        <div
          className="rounded-lg border p-2 flex items-center justify-center gap-3"
          style={{ borderColor: "var(--card-border)", backgroundColor: "var(--card)" }}
        >
          <Button variant="ghost" size="sm" onClick={skipBack} title="Back 5s">
            <SkipBack className="w-4 h-4" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={togglePlay}
            disabled={isAudioLoading}
            className="w-10 h-10 rounded-full"
          >
            {isPlaying ? <Pause className="w-5 h-5" /> : <Play className="w-5 h-5" />}
          </Button>
          <Button variant="ghost" size="sm" onClick={skipForward} title="Forward 5s">
            <SkipForward className="w-4 h-4" />
          </Button>
          <span className="text-xs font-mono text-muted-foreground min-w-[80px] text-center">
            {formatTime(currentTime)} / {formatTime(currentDuration)}
          </span>
        </div>

        {/* Edit history (collapsible) */}
        {hasEdits && (
          <div
            className="rounded-lg border"
            style={{ borderColor: "var(--card-border)", backgroundColor: "var(--card)" }}
          >
            <button
              onClick={() => setShowHistory(!showHistory)}
              className="w-full flex items-center gap-2 p-2 text-sm font-medium"
              style={{ color: "var(--text)" }}
            >
              {showHistory ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
              Edit History ({editStack.length} edit{editStack.length !== 1 ? "s" : ""})
              <span className="ml-auto text-xs text-muted-foreground">
                {formatTime(audioInfo.original_duration_seconds)} → {formatTime(audioInfo.current_duration_seconds)}
              </span>
            </button>
            {showHistory && (
              <div className="border-t px-3 py-2 space-y-1" style={{ borderColor: "var(--card-border)" }}>
                {editStack.map((edit, i) => (
                  <div key={edit.edit_id} className="flex items-center gap-2 text-xs">
                    <span className="text-muted-foreground w-4 text-right">{i + 1}.</span>
                    <span className="font-medium capitalize" style={{ color: "var(--text)" }}>
                      {edit.operation.replace(/_/g, " ")}
                    </span>
                    <span className="text-muted-foreground">
                      {formatTime(edit.duration_before)} → {formatTime(edit.duration_after)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Operating indicator */}
        {isOperating && (
          <div className="flex items-center justify-center gap-2 text-sm text-muted-foreground">
            <Spinner className="w-4 h-4" />
            Processing...
          </div>
        )}
      </main>

      {/* Submit confirmation dialog */}
      {showSubmitConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div
            className="rounded-lg border p-6 max-w-sm w-full space-y-4"
            style={{ borderColor: "var(--card-border)", backgroundColor: "var(--card)" }}
          >
            <h2 className="text-lg font-semibold" style={{ color: "var(--text)" }}>
              {t('submitConfirmation')}
            </h2>
            <div className="space-y-1 text-sm text-muted-foreground">
              <p>Original duration: {formatTime(audioInfo.original_duration_seconds)}</p>
              <p>Edited duration: {formatTime(audioInfo.current_duration_seconds)}</p>
              <p>Edits applied: {editStack.length}</p>
            </div>
            <p className="text-xs text-muted-foreground">
              The edited audio will be used for separation and lyrics transcription. The original is preserved.
            </p>
            <div className="flex gap-2 justify-end">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setShowSubmitConfirm(false)}
                disabled={isSubmitting}
              >
                Cancel
              </Button>
              <Button
                size="sm"
                onClick={handleSubmit}
                disabled={isSubmitting}
                className="bg-green-600 hover:bg-green-700 text-white"
              >
                {isSubmitting ? <Spinner className="w-4 h-4" /> : "Submit"}
              </Button>
            </div>
          </div>
        </div>
      )}

      {/* Restore dialog */}
      {showRestoreDialog && (
        <AudioEditRestoreDialog
          jobId={job.job_id}
          onRestore={handleRestoreSession}
          onStartFresh={() => setShowRestoreDialog(false)}
        />
      )}

      {/* Restoring indicator */}
      {isRestoringSession && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <div
            className="rounded-lg border p-6 text-center"
            style={{ borderColor: "var(--card-border)", backgroundColor: "var(--card)" }}
          >
            <Spinner className="w-8 h-8 mx-auto mb-3" />
            <p className="font-medium" style={{ color: "var(--text)" }}>Restoring session...</p>
            <p className="text-sm text-muted-foreground mt-1">
              Replaying {editStack.length} edit{editStack.length !== 1 ? "s" : ""}
            </p>
          </div>
        </div>
      )}

      {/* Join dialog */}
      {showJoinDialog && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div
            className="rounded-lg border p-6 max-w-sm w-full space-y-4"
            style={{ borderColor: "var(--card-border)", backgroundColor: "var(--card)" }}
          >
            <h2 className="text-lg font-semibold" style={{ color: "var(--text)" }}>
              Join Audio
            </h2>
            <p className="text-sm text-muted-foreground">
              Upload an audio file to add to the start or end of the current audio.
            </p>
            <input
              type="file"
              accept=".flac,.wav,.mp3,.m4a,.ogg,.opus"
              onChange={(e) => setJoinFile(e.target.files?.[0] ?? null)}
              className="text-sm"
            />
            {joinFile && (
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => handleJoin("start")}
                  disabled={isUploading}
                >
                  {isUploading ? <Spinner className="w-4 h-4" /> : t('addToStart')}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => handleJoin("end")}
                  disabled={isUploading}
                >
                  {isUploading ? <Spinner className="w-4 h-4" /> : t('addToEnd')}
                </Button>
              </div>
            )}
            <Button
              variant="ghost"
              size="sm"
              onClick={() => { setShowJoinDialog(false); setJoinFile(null) }}
              disabled={isUploading}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
