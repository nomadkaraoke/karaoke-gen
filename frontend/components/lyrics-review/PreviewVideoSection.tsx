'use client'

import { useTranslations } from 'next-intl'
import { useState, useEffect, RefObject } from 'react'
import { Button } from '@/components/ui/button'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Loader2 } from 'lucide-react'
import { CorrectionData } from '@/lib/lyrics-review/types'
import { applyOffsetToCorrectionData } from '@/lib/lyrics-review/utils/timingUtils'

const POLL_INTERVAL_MS = 3000
// Cold-starting a fallback encoder VM can take 2-3 minutes; give up after 5.
const ENCODE_TIMEOUT_MS = 5 * 60 * 1000
// After this long, explain the wait (an encoder VM is probably cold-starting).
const SLOW_ENCODE_THRESHOLD_MS = 30 * 1000

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

interface PreviewVideoSectionProps {
  apiClient: ApiClient | null
  isModalOpen: boolean
  updatedData: CorrectionData
  videoRef?: RefObject<HTMLVideoElement>
  timingOffsetMs?: number
  isDuet?: boolean
}

type PreviewState =
  | { status: 'generating' }
  | { status: 'encoding'; slow: boolean }
  | { status: 'ready'; videoUrl: string }
  | { status: 'error'; error: string }

export default function PreviewVideoSection({
  apiClient,
  isModalOpen,
  updatedData,
  videoRef,
  timingOffsetMs = 0,
  isDuet,
}: PreviewVideoSectionProps) {
  const t = useTranslations('lyricsReview.previewVideo')
  const [previewState, setPreviewState] = useState<PreviewState>({ status: 'generating' })
  const [retryNonce, setRetryNonce] = useState(0)

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

  if (!apiClient) return null

  return (
    <div className="mb-4">
      {previewState.status === 'generating' && (
        <div className="flex items-center gap-3 p-4">
          <Loader2 className="h-5 w-5 animate-spin" />
          <span>{t('generating')}</span>
        </div>
      )}

      {previewState.status === 'encoding' && (
        <div className="flex flex-col items-center justify-center p-8 text-center">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-gray-200 border-t-blue-500 mb-4" />
          <p className="text-sm">{t('encoding')}</p>
          {previewState.slow && (
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
            ref={videoRef as RefObject<HTMLVideoElement>}
            controls
            autoPlay
            src={previewState.videoUrl}
            className="block w-full h-auto"
          >
            {t('unsupportedBrowser')}
          </video>
        </div>
      )}
    </div>
  )
}
