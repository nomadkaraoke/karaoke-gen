'use client'

import { useTranslations } from 'next-intl'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { CorrectionData } from '@/lib/lyrics-review/types'
import { AlertTriangle, ArrowLeft, ArrowRight, Loader2 } from 'lucide-react'
import PreviewVideoSection from '../PreviewVideoSection'

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

interface ReviewChangesModalProps {
  open: boolean
  onClose: () => void
  data: CorrectionData
  onSubmit: () => void
  isSubmitting?: boolean
  apiClient?: ApiClient | null
  timingOffsetMs?: number
  isDuet?: boolean
  /** When true (e.g. tenant / uploaded-instrumental jobs) approving here completes the
   *  track directly — there is no instrumental-review step — so the CTA reflects that. */
  completesReview?: boolean
  /** Per-screen skip (C1): the backing decision was confidently auto-resolved, so
   *  approving here can complete the whole job and the instrumental screen is skipped.
   *  Shows a "review instrumental anyway" escape hatch. */
  autoInstrumentalConfident?: boolean
  /** The server-resolved instrumental ("clean" | "with_backing" | ...) for the note. */
  autoInstrumentalSelection?: string | null
  /** Whether the reviewer opted back into the instrumental screen. */
  reviewInstrumentalAnyway?: boolean
  onToggleReviewInstrumental?: (val: boolean) => void
}

export default function ReviewChangesModal({
  open,
  onClose,
  data,
  onSubmit,
  isSubmitting = false,
  apiClient = null,
  timingOffsetMs = 0,
  isDuet,
  completesReview = false,
  autoInstrumentalConfident = false,
  autoInstrumentalSelection = null,
  reviewInstrumentalAnyway = false,
  onToggleReviewInstrumental,
}: ReviewChangesModalProps) {
  const t = useTranslations('lyricsReview.modals.reviewChanges')
  const corrections = data.corrections || []
  const totalSegments = data.corrected_segments?.length || 0
  const hasNoLyrics = totalSegments === 0

  // Check if there are manual corrections (user-made changes)
  const hasManualCorrections = corrections.some(c => c.handler === 'ManualCorrector' || c.handler === 'UserEdit')

  const handleSubmit = () => {
    if (isSubmitting) return
    onSubmit()
  }

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t('previewWithVocals')}</DialogTitle>
        </DialogHeader>

        {/* Video Preview Section */}
        <PreviewVideoSection
          apiClient={apiClient}
          isModalOpen={open}
          updatedData={data}
          timingOffsetMs={timingOffsetMs}
          isDuet={isDuet}
        />

        {/* No lyrics warning */}
        {hasNoLyrics && (
          <div className="flex items-start gap-3 rounded-lg border border-yellow-500/50 bg-yellow-500/10 p-4">
            <AlertTriangle className="h-5 w-5 text-yellow-500 mt-0.5 shrink-0" />
            <div className="text-sm space-y-2">
              <p className="font-medium text-yellow-500">{t('noLyricsTitle')}</p>
              <p className="text-muted-foreground">
                {t('noLyricsDesc')}
              </p>
              <p className="text-muted-foreground">
                {t('noLyricsHint')}
              </p>
            </div>
          </div>
        )}

        {/* Info text */}
        {!hasNoLyrics && (
          <div className="text-sm text-muted-foreground space-y-1">
            {hasManualCorrections ? (
              <p>{t('manualCorrectionsDetected')}</p>
            ) : (
              <p>{t('noManualCorrections')}</p>
            )}
            <p>{t('totalSegments', { count: totalSegments })}</p>
          </div>
        )}

        {/* Per-screen skip (C1): backing decision auto-resolved — the instrumental
            screen is skipped unless the reviewer opts back in. */}
        {autoInstrumentalConfident && (
          <div className="rounded-lg border border-purple-500/40 bg-purple-500/5 p-3 text-sm space-y-2">
            <p className="text-muted-foreground">
              {autoInstrumentalSelection === 'with_backing'
                ? t('autoInstrumentalBacking')
                : t('autoInstrumentalClean')}
            </p>
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                className="h-4 w-4"
                checked={reviewInstrumentalAnyway}
                onChange={(e) => onToggleReviewInstrumental?.(e.target.checked)}
              />
              <span>{t('reviewInstrumentalAnyway')}</span>
            </label>
          </div>
        )}

        <DialogFooter className="border-t pt-4">
          <Button variant="ghost" onClick={onClose} disabled={isSubmitting} className="text-primary">
            <ArrowLeft className="h-4 w-4 mr-2" />
            Cancel
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={isSubmitting || hasNoLyrics}
            className="bg-green-600 hover:bg-green-700"
          >
            {isSubmitting ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                {t('saving')}
              </>
            ) : (
              <>
                {completesReview ? t('completeTrack') : t('proceedToInstrumental')}
                <ArrowRight className="h-4 w-4 ml-2" />
              </>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
