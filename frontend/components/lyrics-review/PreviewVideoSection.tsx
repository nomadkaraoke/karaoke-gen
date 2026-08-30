'use client'

import { useTranslations } from 'next-intl'
import {
  useState,
  useEffect,
  useRef,
  useCallback,
  useImperativeHandle,
  forwardRef,
  RefObject,
} from 'react'
import { Button } from '@/components/ui/button'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Loader2 } from 'lucide-react'
import { CorrectionData, InstrumentalOption } from '@/lib/lyrics-review/types'
import { applyOffsetToCorrectionData } from '@/lib/lyrics-review/utils/timingUtils'

const POLL_INTERVAL_MS = 3000
// Cold-starting a fallback encoder VM can take 2-3 minutes; give up after 5.
const ENCODE_TIMEOUT_MS = 5 * 60 * 1000
// After this long, explain the wait (an encoder VM is probably cold-starting).
const SLOW_ENCODE_THRESHOLD_MS = 30 * 1000
// Keep the overlaid instrumental audio within this many seconds of the video.
const AUDIO_SYNC_TOLERANCE_S = 0.25

interface ApiClient {
  generatePreviewVideo: (data: CorrectionData, isDuet?: boolean) => Promise<{
    status: string
    message?: string
    preview_hash?: string
  }>
  getPreviewVideoStatus: (hash: string) => Promise<{
    status: string
    message?: string
  }>
  getPreviewVideoUrl: (hash: string) => string
}

export type AudioMode = 'original' | 'instrumental'

export interface PreviewVideoHandle {
  /** Seek the preview to `time` and switch audio to the instrumental track. */
  switchToInstrumentalAndSeek: (time: number) => void
}

interface PreviewVideoSectionProps {
  apiClient: ApiClient | null
  isModalOpen: boolean
  updatedData: CorrectionData
  videoRef?: RefObject<HTMLVideoElement>
  timingOffsetMs?: number
  isDuet?: boolean
  /** Instrumental stem options (from the combined-review correction data). */
  instrumentalOptions?: InstrumentalOption[]
  /** The auto-selected instrumental id ("clean" | "with_backing"). */
  autoSelection?: string | null
  /** Reports the video's current playback time (drives the backing-vocals playhead). */
  onTimeUpdate?: (currentTime: number) => void
}

type PreviewState =
  | { status: 'generating' }
  | { status: 'encoding'; slow: boolean }
  | { status: 'ready'; videoUrl: string }
  | { status: 'error'; error: string }

function PreviewVideoSection(
  {
    apiClient,
    isModalOpen,
    updatedData,
    videoRef,
    timingOffsetMs = 0,
    isDuet,
    instrumentalOptions,
    autoSelection,
    onTimeUpdate,
  }: PreviewVideoSectionProps,
  ref: React.Ref<PreviewVideoHandle>
) {
  const t = useTranslations('lyricsReview.previewVideo')
  const [previewState, setPreviewState] = useState<PreviewState>({ status: 'generating' })
  const [retryNonce, setRetryNonce] = useState(0)
  const [audioMode, setAudioMode] = useState<AudioMode>('original')

  const internalVideoRef = useRef<HTMLVideoElement | null>(null)
  const instrumentalAudioRef = useRef<HTMLAudioElement | null>(null)
  const audioModeRef = useRef<AudioMode>('original')
  audioModeRef.current = audioMode

  // The instrumental track the toggle switches to: the auto-selected option,
  // falling back to the clean instrumental (or the first available option).
  const instrumentalOption =
    instrumentalOptions?.find((o) => o.id === autoSelection) ??
    instrumentalOptions?.find((o) => o.id === 'clean') ??
    instrumentalOptions?.[0]
  const instrumentalUrl = instrumentalOption?.audio_url ?? null

  const setVideoEl = useCallback(
    (el: HTMLVideoElement | null) => {
      internalVideoRef.current = el
      if (videoRef) {
        ;(videoRef as React.MutableRefObject<HTMLVideoElement | null>).current = el
      }
    },
    [videoRef]
  )

  // Generate preview when modal opens (or when Retry is clicked).
  // The POST returns quickly: "success" when the video already exists (cache
  // hit / local render), or "generating" when encoding continues on a GCE
  // worker — in that case we poll the status endpoint until the mp4 exists.
  useEffect(() => {
    if (!isModalOpen || !apiClient) return

    let cancelled = false
    let pollTimer: ReturnType<typeof setTimeout> | undefined

    const sleep = (ms: number) =>
      new Promise<void>((resolve) => {
        pollTimer = setTimeout(resolve, ms)
      })

    const setReady = (hash: string) => {
      setPreviewState({ status: 'ready', videoUrl: apiClient.getPreviewVideoUrl(hash) })
    }

    const pollUntilReady = async (hash: string) => {
      const startedAt = Date.now()
      while (!cancelled) {
        await sleep(POLL_INTERVAL_MS)
        if (cancelled) return
        if (Date.now() - startedAt > ENCODE_TIMEOUT_MS) {
          setPreviewState({ status: 'error', error: t('timedOut') })
          return
        }
        try {
          const result = await apiClient.getPreviewVideoStatus(hash)
          if (cancelled) return
          if (result.status === 'ready') {
            setReady(hash)
            return
          }
          if (result.status === 'error') {
            setPreviewState({ status: 'error', error: result.message || t('failed') })
            return
          }
        } catch {
          // Transient poll failure (network blip) — keep polling until timeout
        }
        setPreviewState({
          status: 'encoding',
          slow: Date.now() - startedAt > SLOW_ENCODE_THRESHOLD_MS,
        })
      }
    }

    const generatePreview = async () => {
      setPreviewState({ status: 'generating' })
      try {
        // Apply timing offset if needed
        const dataToPreview =
          timingOffsetMs !== 0
            ? applyOffsetToCorrectionData(updatedData, timingOffsetMs)
            : updatedData

        const response = await apiClient.generatePreviewVideo(dataToPreview, isDuet)
        if (cancelled) return

        if (response.status === 'error' || !response.preview_hash) {
          setPreviewState({
            status: 'error',
            error: response.message || t('failed'),
          })
          return
        }

        if (response.status === 'generating') {
          setPreviewState({ status: 'encoding', slow: false })
          await pollUntilReady(response.preview_hash)
          return
        }

        setReady(response.preview_hash)
      } catch (error) {
        if (!cancelled) {
          setPreviewState({
            status: 'error',
            error: (error as Error).message || t('failed'),
          })
        }
      }
    }

    generatePreview()

    return () => {
      cancelled = true
      if (pollTimer) clearTimeout(pollTimer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `t` is not referentially stable across renders
  }, [isModalOpen, apiClient, updatedData, timingOffsetMs, isDuet, retryNonce])

  // Reset audio mode back to original each time a fresh preview starts encoding.
  useEffect(() => {
    if (previewState.status !== 'ready') setAudioMode('original')
  }, [previewState.status])

  // Keep the overlaid instrumental audio in lock-step with the video element.
  // The video always carries the original (with-vocals) audio; when the user
  // switches to "instrumental" we mute the video and play the stem in sync so
  // the position is preserved on toggle.
  useEffect(() => {
    const video = internalVideoRef.current
    const audio = instrumentalAudioRef.current
    if (!video) return

    const instrumentalActive = () => audioModeRef.current === 'instrumental' && !!audio

    const applyMode = () => {
      const active = instrumentalActive()
      video.muted = active
      if (!audio) return
      audio.muted = !active
      if (active) {
        audio.playbackRate = video.playbackRate
        if (Math.abs(audio.currentTime - video.currentTime) > AUDIO_SYNC_TOLERANCE_S) {
          audio.currentTime = video.currentTime
        }
        if (!video.paused) audio.play()?.catch(() => {})
      } else {
        audio.pause()
      }
    }

    const onPlay = () => {
      if (!audio || !instrumentalActive()) return
      audio.currentTime = video.currentTime
      audio.play()?.catch(() => {})
    }
    const onPause = () => audio?.pause()
    const onSeeked = () => {
      if (audio) audio.currentTime = video.currentTime
    }
    const onRateChange = () => {
      if (audio) audio.playbackRate = video.playbackRate
    }
    const onTimeUpdateEvt = () => {
      onTimeUpdate?.(video.currentTime)
      if (!audio || !instrumentalActive()) return
      if (Math.abs(audio.currentTime - video.currentTime) > AUDIO_SYNC_TOLERANCE_S) {
        audio.currentTime = video.currentTime
      }
    }

    applyMode()
    video.addEventListener('play', onPlay)
    video.addEventListener('pause', onPause)
    video.addEventListener('seeked', onSeeked)
    video.addEventListener('ratechange', onRateChange)
    video.addEventListener('timeupdate', onTimeUpdateEvt)
    return () => {
      video.removeEventListener('play', onPlay)
      video.removeEventListener('pause', onPause)
      video.removeEventListener('seeked', onSeeked)
      video.removeEventListener('ratechange', onRateChange)
      video.removeEventListener('timeupdate', onTimeUpdateEvt)
    }
  }, [audioMode, previewState.status, instrumentalUrl, onTimeUpdate])

  useImperativeHandle(
    ref,
    () => ({
      switchToInstrumentalAndSeek: (time: number) => {
        const video = internalVideoRef.current
        if (!video || !Number.isFinite(time)) return
        if (instrumentalUrl) setAudioMode('instrumental')
        video.currentTime = Math.max(0, time)
        video.play()?.catch(() => {})
      },
    }),
    [instrumentalUrl]
  )

  if (!apiClient) return null

  const showToggle = previewState.status === 'ready' && !!instrumentalUrl

  return (
    <div className="mb-4">
      {(previewState.status === 'generating' || previewState.status === 'encoding') && (
        <div className="flex flex-col items-center justify-center p-8 text-center">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-gray-200 border-t-blue-500 mb-4" />
          <p className="text-sm">
            {previewState.status === 'generating' ? t('generating') : t('encoding')}
          </p>
          {previewState.status === 'encoding' && previewState.slow && (
            <p className="mt-2 text-sm text-gray-600">{t('startingEncoder')}</p>
          )}
        </div>
      )}

      {previewState.status === 'error' && (
        <Alert variant="destructive" className="mb-4">
          <AlertDescription className="flex items-center justify-between">
            <span>{previewState.error}</span>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setRetryNonce((n) => n + 1)}
            >
              {t('retry')}
            </Button>
          </AlertDescription>
        </Alert>
      )}

      {previewState.status === 'ready' && (
        <div className="w-full">
          <video
            ref={setVideoEl}
            controls
            autoPlay
            src={previewState.videoUrl}
            className="block w-full h-auto"
          >
            {t('unsupportedBrowser')}
          </video>
          {instrumentalUrl && (
            // Hidden stem player kept in sync with the video for the audio toggle.
            <audio ref={instrumentalAudioRef} src={instrumentalUrl} preload="auto" />
          )}
        </div>
      )}

      {showToggle && (
        <div className="mt-3 flex items-center justify-center gap-2">
          <span className="text-xs text-muted-foreground mr-1">{t('audioLabel')}</span>
          <div className="inline-flex rounded-md border border-border overflow-hidden">
            <button
              type="button"
              onClick={() => setAudioMode('original')}
              className={`px-3 py-1 text-sm transition-colors ${
                audioMode === 'original'
                  ? 'bg-primary text-primary-foreground'
                  : 'bg-muted text-muted-foreground hover:bg-muted/70'
              }`}
            >
              {t('audioOriginal')}
            </button>
            <button
              type="button"
              onClick={() => setAudioMode('instrumental')}
              className={`px-3 py-1 text-sm transition-colors ${
                audioMode === 'instrumental'
                  ? 'bg-primary text-primary-foreground'
                  : 'bg-muted text-muted-foreground hover:bg-muted/70'
              }`}
            >
              {t('audioInstrumental')}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

export default forwardRef(PreviewVideoSection)
