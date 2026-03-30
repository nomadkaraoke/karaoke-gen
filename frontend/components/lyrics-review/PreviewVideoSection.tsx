'use client'

import { useState, useEffect, RefObject } from 'react'
import { Button } from '@/components/ui/button'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Loader2 } from 'lucide-react'
import { CorrectionData } from '@/lib/lyrics-review/types'
import { applyOffsetToCorrectionData } from '@/lib/lyrics-review/utils/timingUtils'

interface ApiClient {
  generatePreviewVideo: (data: CorrectionData) => Promise<{
    status: string
    message?: string
    preview_hash?: string
  }>
  getPreviewVideoUrl: (hash: string) => string
}

interface PreviewVideoSectionProps {
  apiClient: ApiClient | null
  isModalOpen: boolean
  updatedData: CorrectionData
  videoRef?: RefObject<HTMLVideoElement>
  timingOffsetMs?: number
}

export default function PreviewVideoSection({
  apiClient,
  isModalOpen,
  updatedData,
  videoRef,
  timingOffsetMs = 0,
}: PreviewVideoSectionProps) {
  const [previewState, setPreviewState] = useState<{
    status: 'loading' | 'warming_up' | 'ready' | 'error'
    videoUrl?: string
    error?: string
  }>({ status: 'loading' })

  // Generate preview when modal opens
  useEffect(() => {
    if (isModalOpen && apiClient) {
      const generatePreview = async () => {
        setPreviewState({ status: 'loading' })
        try {
          // Apply timing offset if needed
          const dataToPreview =
            timingOffsetMs !== 0
              ? applyOffsetToCorrectionData(updatedData, timingOffsetMs)
              : updatedData

          const response = await apiClient.generatePreviewVideo(dataToPreview)

          if (response.status === 'worker_starting') {
            setPreviewState({
              status: 'warming_up',
              error: response.message,
            })
            return
          }

          if (response.status === 'error') {
            setPreviewState({
              status: 'error',
              error: response.message || 'Failed to generate preview video',
            })
            return
          }

          if (!response.preview_hash) {
            setPreviewState({
              status: 'error',
              error: 'No preview hash received from server',
            })
            return
          }

          const videoUrl = apiClient.getPreviewVideoUrl(response.preview_hash)
          setPreviewState({
            status: 'ready',
            videoUrl,
          })
        } catch (error) {
          setPreviewState({
            status: 'error',
            error: (error as Error).message || 'Failed to generate preview video',
          })
        }
      }

      generatePreview()
    }
  }, [isModalOpen, apiClient, updatedData, timingOffsetMs])

  if (!apiClient) return null

  return (
    <div className="mb-4">
      {previewState.status === 'loading' && (
        <div className="flex items-center gap-3 p-4">
          <Loader2 className="h-5 w-5 animate-spin" />
          <span>Generating preview video...</span>
        </div>
      )}

      {previewState.status === 'warming_up' && (
        <div className="flex flex-col items-center justify-center p-8 text-center">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-gray-200 border-t-blue-500 mb-4" />
          <p className="text-sm text-gray-600">
            Starting encoding worker... This usually takes about a minute.
          </p>
        </div>
      )}

      {previewState.status === 'error' && (
        <Alert variant="destructive" className="mb-4">
          <AlertDescription className="flex items-center justify-between">
            <span>{previewState.error}</span>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setPreviewState({ status: 'loading' })}
            >
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      )}

      {previewState.status === 'ready' && previewState.videoUrl && (
        <div className="w-full">
          <video
            ref={videoRef as RefObject<HTMLVideoElement>}
            controls
            autoPlay
            src={previewState.videoUrl}
            className="block w-full h-auto"
          >
            Your browser does not support the video tag.
          </video>
        </div>
      )}
    </div>
  )
}
