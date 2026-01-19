'use client'

import { useEffect, useRef, useState, useCallback } from 'react'
import { Button } from '@/components/ui/button'
import { Slider } from '@/components/ui/slider'
import { Play, Pause } from 'lucide-react'

// Extend Window interface for global audio functions
declare global {
  interface Window {
    seekAndPlayAudio?: (time: number) => void
    toggleAudioPlayback?: () => void
    getAudioDuration?: () => number
    isAudioPlaying?: boolean
  }
}

interface AudioPlayerProps {
  audioUrl: string | null
  onTimeUpdate?: (time: number) => void
}

export default function AudioPlayer({ audioUrl, onTimeUpdate }: AudioPlayerProps) {
  const [isPlaying, setIsPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const audioRef = useRef<HTMLAudioElement | null>(null)

  useEffect(() => {
    if (!audioUrl) return

    const audio = new Audio(audioUrl)
    audioRef.current = audio

    let animationFrameId: number

    const updateTime = () => {
      const time = audio.currentTime
      setCurrentTime(time)
      onTimeUpdate?.(time)
      animationFrameId = requestAnimationFrame(updateTime)
    }

    audio.addEventListener('play', () => {
      setIsPlaying(true)
      window.isAudioPlaying = true
      updateTime()
    })

    audio.addEventListener('pause', () => {
      setIsPlaying(false)
      window.isAudioPlaying = false
      cancelAnimationFrame(animationFrameId)
    })

    audio.addEventListener('ended', () => {
      cancelAnimationFrame(animationFrameId)
      setIsPlaying(false)
      window.isAudioPlaying = false
      setCurrentTime(0)
    })

    audio.addEventListener('loadedmetadata', () => {
      setDuration(audio.duration)
    })

    return () => {
      cancelAnimationFrame(animationFrameId)
      audio.pause()
      audio.src = ''
      audioRef.current = null
      window.isAudioPlaying = false
    }
  }, [audioUrl, onTimeUpdate])

  const handlePlayPause = () => {
    if (!audioRef.current) return

    if (isPlaying) {
      audioRef.current.pause()
    } else {
      audioRef.current.play()
    }
    setIsPlaying(!isPlaying)
  }

  const handleSeek = (value: number[]) => {
    if (!audioRef.current) return
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
    if (!audioRef.current) return

    audioRef.current.currentTime = time
    setCurrentTime(time)
    audioRef.current.play()
    setIsPlaying(true)
  }, [])

  const togglePlayback = useCallback(() => {
    if (!audioRef.current) return

    if (isPlaying) {
      audioRef.current.pause()
    } else {
      audioRef.current.play()
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

  return (
    <div className="flex items-center gap-2 bg-card rounded h-8">
      <span className="text-xs text-muted-foreground mr-1">Playback:</span>

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
