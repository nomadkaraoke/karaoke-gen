'use client'

import { useTranslations } from 'next-intl'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Loader2 } from 'lucide-react'

interface WaveformDataResult {
  amplitudes: number[]
  duration_seconds?: number
  duration?: number
}

interface BackingVocalsWaveformProps {
  /** Fetches the backing-vocals stem amplitudes for this job. */
  getWaveformData: (numPoints?: number) => Promise<WaveformDataResult>
  /** Current playback time of the preview video (seconds) — drives the playhead. */
  currentTime: number
  /** Seek the preview to this time and switch audio to the instrumental. */
  onSeek: (time: number) => void
}

const WAVEFORM_HEIGHT = 35
const NUM_POINTS = 600

/**
 * Thin waveform of the whole backing-vocals stem, shown when the instrumental
 * auto-selector chose to keep the backing vocals. Clicking seeks the preview
 * video to that point and switches its audio to the instrumental+backing track,
 * so the reviewer can quickly sanity-check the automatic choice.
 */
export default function BackingVocalsWaveform({
  getWaveformData,
  currentTime,
  onSeek,
}: BackingVocalsWaveformProps) {
  const t = useTranslations('lyricsReview.previewVideo')
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [amplitudes, setAmplitudes] = useState<number[] | null>(null)
  const [duration, setDuration] = useState(0)
  const [failed, setFailed] = useState(false)
  const [canvasWidth, setCanvasWidth] = useState(0)

  // Fetch the backing-vocals amplitudes once on mount.
  useEffect(() => {
    let cancelled = false
    getWaveformData(NUM_POINTS)
      .then((data) => {
        if (cancelled) return
        const dur = data.duration_seconds ?? data.duration ?? 0
        if (!data.amplitudes?.length || dur <= 0) {
          setFailed(true)
          return
        }
        setAmplitudes(data.amplitudes)
        setDuration(dur)
      })
      .catch(() => {
        if (!cancelled) setFailed(true)
      })
    return () => {
      cancelled = true
    }
  }, [getWaveformData])

  // Track container width so the canvas backing store matches its display size.
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const observer = new ResizeObserver((entries) => {
      setCanvasWidth(entries[entries.length - 1].contentRect.width)
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [amplitudes])

  const draw = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas || !amplitudes || canvasWidth <= 0) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const width = canvasWidth
    const height = WAVEFORM_HEIGHT
    const centerY = height / 2
    ctx.clearRect(0, 0, width, height)

    const barWidth = width / amplitudes.length
    // Pink to match the backing-vocals highlight colour used on the full
    // instrumental-selection screen.
    ctx.fillStyle = '#ec4899'
    amplitudes.forEach((amp, i) => {
      const x = i * barWidth
      const barHeight = Math.max(1, amp * height * 0.95)
      ctx.fillRect(x, centerY - barHeight / 2, Math.max(1, barWidth - 0.5), barHeight)
    })

    // Playhead
    if (duration > 0) {
      const playheadX = Math.min(width, Math.max(0, (currentTime / duration) * width))
      ctx.fillStyle = 'rgba(255, 255, 255, 0.9)'
      ctx.fillRect(playheadX - 0.5, 0, 1.5, height)
    }
  }, [amplitudes, canvasWidth, currentTime, duration])

  useEffect(() => {
    draw()
  }, [draw])

  const handleClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    if (!duration) return
    const rect = e.currentTarget.getBoundingClientRect()
    const x = e.clientX - rect.left
    const time = (x / rect.width) * duration
    if (Number.isFinite(time)) onSeek(Math.max(0, time))
  }

  if (failed) return null

  return (
    <div className="mt-2">
      <p className="text-xs text-muted-foreground mb-1">{t('backingVocalsWaveformHint')}</p>
      <div
        ref={containerRef}
        className="w-full rounded-md overflow-hidden bg-[#0d1117]"
        style={{ height: WAVEFORM_HEIGHT }}
      >
        {amplitudes ? (
          <canvas
            ref={canvasRef}
            width={canvasWidth}
            height={WAVEFORM_HEIGHT}
            onClick={handleClick}
            className="block w-full cursor-pointer"
            style={{ height: WAVEFORM_HEIGHT }}
          />
        ) : (
          <div className="flex items-center justify-center h-full">
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
          </div>
        )}
      </div>
    </div>
  )
}
