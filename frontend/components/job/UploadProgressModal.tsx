"use client"

import { useEffect, useRef, useState } from "react"
import { useTranslations } from 'next-intl'
import { AlertTriangle, Loader2, UploadCloud } from "lucide-react"
import { Description as DialogPrimitiveDescription } from "@radix-ui/react-dialog"
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog"
import type { UploadProgress } from "@/lib/api"

const MB = 1024 * 1024
/** Rolling window used for the speed estimate — long enough to smooth XHR progress jitter. */
const SPEED_WINDOW_MS = 5000
/** Don't show a speed/ETA until we have at least this much history. */
const MIN_SAMPLE_SPAN_MS = 1000

export interface ProgressSample {
  at: number
  loaded: number
}

/**
 * Bytes/sec over the samples' span and seconds remaining, or null while there
 * isn't enough history to say anything honest.
 */
export function estimateTransfer(
  samples: ProgressSample[],
  total: number,
): { bytesPerSec: number; secondsLeft: number } | null {
  if (samples.length < 2) return null
  const first = samples[0]
  const last = samples[samples.length - 1]
  const spanMs = last.at - first.at
  if (spanMs < MIN_SAMPLE_SPAN_MS) return null
  const bytesPerSec = ((last.loaded - first.loaded) / spanMs) * 1000
  if (bytesPerSec <= 0) return null
  return { bytesPerSec, secondsLeft: Math.max(0, (total - last.loaded) / bytesPerSec) }
}

/**
 * Blocking modal shown while a submitted audio file uploads from the browser.
 * The job already exists server-side but can't start until these bytes land,
 * so this can't be dismissed — leaving the page kills the upload (the parent
 * also installs a beforeunload guard).
 */
export function UploadProgressModal({ progress }: { progress: UploadProgress }) {
  const t = useTranslations('jobFlow')
  const samplesRef = useRef<ProgressSample[]>([])
  const [estimate, setEstimate] = useState<ReturnType<typeof estimateTransfer>>(null)

  useEffect(() => {
    if (progress.phase !== 'uploading') return
    const now = Date.now()
    const samples = samplesRef.current
    samples.push({ at: now, loaded: progress.loaded })
    while (samples.length > 2 && now - samples[0].at > SPEED_WINDOW_MS) samples.shift()
    setEstimate(estimateTransfer(samples, progress.total))
  }, [progress.phase, progress.loaded, progress.total])

  const percent = progress.total > 0 ? Math.min(100, Math.round((progress.loaded / progress.total) * 100)) : 0
  const barPercent = progress.phase === 'creating' ? 0 : progress.phase === 'finalizing' ? 100 : percent

  let label: string
  if (progress.phase === 'uploading') {
    label = t('uploadingAudioPercent', { percent })
  } else if (progress.phase === 'finalizing') {
    label = t('uploadFinalizing')
  } else {
    label = t('uploadCreatingJob')
  }

  let etaText: string | null = null
  if (progress.phase === 'uploading') {
    if (!estimate) {
      etaText = t('uploadEtaEstimating')
    } else {
      const secs = Math.ceil(estimate.secondsLeft)
      etaText = secs >= 60
        ? t('uploadEtaMinutes', { minutes: Math.floor(secs / 60), seconds: secs % 60 })
        : t('uploadEtaSeconds', { seconds: secs })
    }
  }

  const block = (e: Event) => e.preventDefault()

  return (
    <Dialog open onOpenChange={() => { /* not dismissable while uploading */ }}>
      <DialogContent
        showCloseButton={false}
        onEscapeKeyDown={block}
        onPointerDownOutside={block}
        onInteractOutside={block}
        className="max-w-md"
        data-testid="upload-progress-modal"
      >
        <DialogTitle className="flex items-center gap-2 text-foreground">
          <UploadCloud className="w-5 h-5 text-[var(--brand-pink)]" />
          {t('uploadModalTitle')}
        </DialogTitle>

        <div className="space-y-2" role="status" aria-live="polite">
          <div className="flex items-center gap-2 text-sm font-medium" style={{ color: 'var(--text)' }}>
            <Loader2 className="w-4 h-4 animate-spin shrink-0" />
            {label}
          </div>
          <div
            className="h-3 w-full rounded-full overflow-hidden"
            style={{ backgroundColor: 'var(--secondary)' }}
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={barPercent}
          >
            <div
              className={`h-full bg-[var(--brand-pink)] transition-[width] duration-300 ${progress.phase === 'uploading' ? '' : 'animate-pulse'}`}
              style={{ width: `${Math.max(barPercent, 2)}%` }}
            />
          </div>
          {progress.phase === 'uploading' && (
            <div className="flex items-center justify-between gap-3 text-xs tabular-nums" style={{ color: 'var(--text-muted)' }}>
              <span>
                {t('uploadSizeProgress', { loaded: (progress.loaded / MB).toFixed(1), total: (progress.total / MB).toFixed(1) })}
                {estimate && <> · {t('uploadSpeed', { speed: (estimate.bytesPerSec / MB).toFixed(1) })}</>}
              </span>
              <span>{etaText}</span>
            </div>
          )}
        </div>

        {/* Radix primitive directly: the shared DialogDescription drops className. */}
        <DialogPrimitiveDescription asChild>
          <p className="flex items-start gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-300">
            <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
            {t('uploadKeepTabOpen')}
          </p>
        </DialogPrimitiveDescription>
      </DialogContent>
    </Dialog>
  )
}
