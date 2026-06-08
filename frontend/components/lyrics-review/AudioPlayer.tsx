'use client'

import { useEffect, useRef, useState, useCallback } from 'react'
import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import { Slider } from '@/components/ui/slider'
import { Progress } from '@/components/ui/progress'
import { Play, Pause, Loader2, AlertCircle } from 'lucide-react'

// Extend Window interface for global audio functions
declare global {
  interface Window {
    seekAndPlayAudio?: (time: number) => void
    toggleAudioPlayback?: () => void
    getAudioDuration?: () => number
    isAudioPlaying?: boolean
    /** True once the media is ready to play through (see AUDIO_READY_EVENT). */
    isAudioReady?: boolean
    /** Buffered fraction of the audio file, 0..1. */
    audioLoadProgress?: number
  }
}

/**
 * Dispatched on `window` whenever audio readiness or load progress changes.
 * detail: { ready: boolean, progress: number }. Subscribe via useAudioReady().
 */
export const AUDIO_READY_EVENT = 'karaoke:audio-ready'

interface AudioPlayerProps {
  audioUrl: string | null
  onTimeUpdate?: (time: number) => void
}

export default function AudioPlayer({ audioUrl, onTimeUpdate }: AudioPlayerProps) {
  const t = useTranslations('lyricsReview.header')
  const [isPlaying, setIsPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [isReady, setIsReady] = useState(false)
  const [loadProgress, setLoadProgress] = useState(0) // 0..1 buffered fraction
  const [hasError, setHasError] = useState(false)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  // Ref mirror so the imperative globals always read current readiness.
  const isReadyRef = useRef(false)

  // Broadcast readiness to imperative consumers (window globals) and to
  // reactive subscribers (useAudioReady hook) via a CustomEvent.
  const publishReady = useCallback((ready: boolean, progress: number) => {
    isReadyRef.current = ready
    if (typeof window === 'undefined') return
    window.isAudioReady = ready
    window.audioLoadProgress = progress
    window.dispatchEvent(new CustomEvent(AUDIO_READY_EVENT, { detail: { ready, progress } }))
  }, [])

  useEffect(() => {
    if (!audioUrl) {
      publishReady(false, 0)
      return
    }

    const audio = new Audio(audioUrl)
    audio.preload = 'auto'
    audioRef.current = audio

    // Reset readiness for the new source.
    setIsReady(false)
    setHasError(false)
    setLoadProgress(0)
    publishReady(false, 0)

    let animationFrameId: number

    const updateTime = () => {
      const time = audio.currentTime
      setCurrentTime(time)
      onTimeUpdate?.(time)
      animationFrameId = requestAnimationFrame(updateTime)
    }

    const markReady = () => {
      if (isReadyRef.current) return
      setIsReady(true)
      setLoadProgress(1)
      publishReady(true, 1)
    }

    const updateProgress = () => {
      const d = audio.duration
      if (!d || !isFinite(d) || audio.buffered.length === 0) return
      const bufferedEnd = audio.buffered.end(audio.buffered.length - 1)
      const frac = Math.min(1, bufferedEnd / d)
      setLoadProgress(frac)
      if (!isReadyRef.current) publishReady(false, frac)
      // canplaythrough is unreliable across browsers — treat fully-buffered as ready too.
      if (bufferedEnd >= d - 0.25) markReady()
    }

    const handlers: Array<[string, EventListener]> = [
      ['play', () => { setIsPlaying(true); window.isAudioPlaying = true; updateTime() }],
      ['pause', () => { setIsPlaying(false); window.isAudioPlaying = false; cancelAnimationFrame(animationFrameId) }],
      ['ended', () => { cancelAnimationFrame(animationFrameId); setIsPlaying(false); window.isAudioPlaying = false; setCurrentTime(0) }],
      ['loadedmetadata', () => setDuration(audio.duration)],
      ['durationchange', () => setDuration(audio.duration)],
      ['progress', updateProgress],
      ['canplaythrough', markReady],
      ['error', () => { setHasError(true); setIsReady(false); publishReady(false, 0) }],
    ]
    handlers.forEach(([ev, fn]) => audio.addEventListener(ev, fn))

    return () => {
      cancelAnimationFrame(animationFrameId)
      handlers.forEach(([ev, fn]) => audio.removeEventListener(ev, fn))
      audio.pause()
      audio.src = ''
      audioRef.current = null
      window.isAudioPlaying = false
      publishReady(false, 0)
    }
  }, [audioUrl, onTimeUpdate, publishReady])

  const handlePlayPause = () => {
    if (!audioRef.current || !isReadyRef.current) return

    if (isPlaying) {
      audioRef.current.pause()
    } else {
      audioRef.current.play().catch(() => {})
    }
    setIsPlaying(!isPlaying)
  }

  const handleSeek = (value: number[]) => {
    if (!audioRef.current || !isReadyRef.current) return
    const time = value[0]
    audioRef.current.currentTime = time
    setCurrentTime(time)
  }

  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60)
    const secs = Math.floor(seconds % 60)
    return `${mins}:${secs.toString().padStart(2, '0')}`
  }

  const seekAndPlay = useCallback((time: number) => {
    if (!audioRef.current || !isReadyRef.current) return

    audioRef.current.currentTime = time
    setCurrentTime(time)
    audioRef.current.play().catch(() => {})
    setIsPlaying(true)
  }, [])

  const togglePlayback = useCallback(() => {
    if (!audioRef.current || !isReadyRef.current) return

    if (isPlaying) {
      audioRef.current.pause()
    } else {
      audioRef.current.play().catch(() => {})
    }
    setIsPlaying(!isPlaying)
  }, [isPlaying])

  // Expose methods globally
  useEffect(() => {
    if (!audioUrl) return

    window.seekAndPlayAudio = seekAndPlay
    window.toggleAudioPlayback = togglePlayback
    window.getAudioDuration = () => duration

    return () => {
      delete window.seekAndPlayAudio
      delete window.toggleAudioPlayback
      delete window.getAudioDuration
    }
  }, [audioUrl, seekAndPlay, togglePlayback, duration])

  if (!audioUrl) return null

  // Failed to load — surface a clear, non-blocking error in place of controls.
  if (hasError) {
    return (
      <div className="flex items-center gap-2 bg-card rounded h-8 text-xs text-destructive">
        <AlertCircle className="h-4 w-4" />
        <span>{t('audioLoadError')}</span>
      </div>
    )
  }

  // Still loading — show a spinner + buffer progress and keep controls disabled.
  if (!isReady) {
    return (
      <div className="flex items-center gap-2 bg-card rounded h-8" aria-busy="true">
        <span className="text-xs text-muted-foreground mr-1">{t('playback')}</span>
        <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
        <span className="text-xs text-muted-foreground">{t('loadingAudio')}</span>
        <Progress value={Math.round(loadProgress * 100)} className="w-[100px] mx-1 h-1.5" />
      </div>
    )
  }

  return (
    <div className="flex items-center gap-2 bg-card rounded h-8">
      <span className="text-xs text-muted-foreground mr-1">{t('playback')}</span>

      <Button variant="ghost" size="icon" className="h-7 w-7 p-0.5" onClick={handlePlayPause}>
        {isPlaying ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
      </Button>

      <span className="text-xs min-w-[32px]">{formatTime(currentTime)}</span>

      <Slider
        value={[currentTime]}
        min={0}
        max={duration || 100}
        step={0.1}
        onValueChange={handleSeek}
        className="w-[100px] mx-1"
      />

      <span className="text-xs min-w-[32px]">{formatTime(duration)}</span>
    </div>
  )
}
