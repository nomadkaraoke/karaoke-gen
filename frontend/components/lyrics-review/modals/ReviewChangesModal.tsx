'use client'

import { useTranslations } from 'next-intl'
import { useRef, useState } from 'react'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { CorrectionData } from '@/lib/lyrics-review/types'
import { AlertTriangle, ArrowLeft, ArrowRight, Loader2 } from 'lucide-react'
import PreviewVideoSection, { PreviewVideoHandle } from '../PreviewVideoSection'
import BackingVocalsWaveform from '../BackingVocalsWaveform'

interface WaveformDataResult {
  amplitudes: number[]
  duration_seconds?: number
  duration?: number
}

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
  getWaveformData?: (numPoints?: number) => Promise<WaveformDataResult>
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
  /** Inline single-click override, driven by the "Audio:" toggle's clean pill:
   *  true once the reviewer has switched the auto-selected backing instrumental to
   *  the clean one. Only offered when the verdict is with_backing and a clean stem
   *  exists (no need to show it when clean is already the default). */
  cleanOverride?: boolean
  onInstrumentalChoiceChange?: (choice: 'clean' | 'with_backing') => void
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
  cleanOverride = false,
  onInstrumentalChoiceChange,
}: ReviewChangesModalProps) {
  const t = useTranslations('lyricsReview.modals.reviewChanges')
  // Instrumental variant labels are shared with the preview toggle.
  const tPreview = useTranslations('lyricsReview.previewVideo')
  const corrections = data.corrections || []
  const totalSegments = data.corrected_segments?.length || 0
  const hasNoLyrics = totalSegments === 0

  // Check if there are manual corrections (user-made changes)
  const hasManualCorrections = corrections.some(c => c.handler === 'ManualCorrector' || c.handler === 'UserEdit')

  const previewRef = useRef<PreviewVideoHandle>(null)
  const [previewTime, setPreviewTime] = useState(0)
  const instrumentalOptions = data.instrumental_options
  const getWaveformData = apiClient?.getWaveformData
  // Only offer the backing waveform (which clicks through to the backing stem)
  // when that stem is actually playable — otherwise the seek would switch audio
  // to the wrong/absent track.
  const hasBackingStem = !!instrumentalOptions?.some(
    (o) => o.id === 'with_backing' && o.audio_url
  )
  const hasCleanStem = !!instrumentalOptions?.some(
    (o) => o.id === 'clean' && o.audio_url
  )
  const showBackingWaveform =
    autoInstrumentalConfident &&
    autoInstrumentalSelection === 'with_backing' &&
    hasBackingStem &&
    !!getWaveformData

  // Split the audio toggle into "Instrumental + backing vocals" / "Clean
  // instrumental" pills only when the auto-selection is the backing instrumental
  // AND a clean stem exists to switch to (i.e. the two are meaningfully
  // different). When clean is already the default there is nothing to offer.
  const offerInstrumentalChoice =
    autoInstrumentalConfident &&
    autoInstrumentalSelection === 'with_backing' &&
    hasCleanStem &&
    hasBackingStem
  const cleanChosen = offerInstrumentalChoice && cleanOverride

  // The three mutually-exclusive final-output outcomes, surfaced as one radio
  // group so it's unambiguous which instrumental the produced video will use.
  //  - 'auto'     → the auto-selected instrumental (backing, or clean when that's
  //                 the verdict). Submits "auto"/backing.
  //  - 'clean'    → override to the clean instrumental (only offered when backing
  //                 was auto-picked and a clean stem exists). Submits "clean".
  //  - 'advanced' → opt into the full instrumental screen (mute sections / upload).
  const decision: 'auto' | 'clean' | 'advanced' = reviewInstrumentalAnyway
    ? 'advanced'
    : cleanChosen
      ? 'clean'
      : 'auto'

  const selectAuto = () => {
    onToggleReviewInstrumental?.(false)
    if (autoInstrumentalSelection === 'with_backing') {
      onInstrumentalChoiceChange?.('with_backing')
      previewRef.current?.auditionInstrumental('with_backing')
    } else {
      previewRef.current?.auditionInstrumental('clean')
    }
  }
  const selectClean = () => {
    onToggleReviewInstrumental?.(false)
    onInstrumentalChoiceChange?.('clean')
    previewRef.current?.auditionInstrumental('clean')
  }
  const selectAdvanced = () => onToggleReviewInstrumental?.(true)

  const handleSubmit = () => {
    if (isSubmitting) return
    onSubmit()
  }

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{t('previewTitle')}</DialogTitle>
        </DialogHeader>

        {/* Video Preview Section */}
        <PreviewVideoSection
          ref={previewRef}
          apiClient={apiClient}
          isModalOpen={open}
          updatedData={data}
          timingOffsetMs={timingOffsetMs}
          isDuet={isDuet}
          instrumentalOptions={instrumentalOptions}
          autoSelection={autoInstrumentalSelection}
          onTimeUpdate={setPreviewTime}
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

        {/* Info text — only surfaced when the user made manual edits. */}
        {!hasNoLyrics && hasManualCorrections && (
          <div className="text-sm text-muted-foreground">
            <p>{t('manualCorrectionsDetected')}</p>
          </div>
        )}

        {/* Per-screen skip (C1): the backing decision was auto-resolved, so this
            modal completes the whole job. The single radio group makes the
            final-output choice explicit (and the escape hatch is just another
            option — "Advanced mode"), separate from the preview-audio toggle
            above which only controls what you hear right now. */}
        {autoInstrumentalConfident && (
          <div className="rounded-lg border border-purple-500/40 bg-purple-500/5 p-3 text-sm space-y-3">
            <p className="font-medium">🎬 {t('finalChoiceLabel')}</p>
            <div role="radiogroup" className="space-y-2">
              {/* Recommended: the auto-selected instrumental */}
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="final-instrumental"
                  className="h-4 w-4"
                  checked={decision === 'auto'}
                  onChange={selectAuto}
                />
                <span>
                  {autoInstrumentalSelection === 'with_backing'
                    ? tPreview('audioInstrumentalBacking')
                    : tPreview('audioInstrumentalClean')}
                </span>
                <span className="text-xs text-green-600 dark:text-green-400 whitespace-nowrap">
                  ✓ {t('recommended')}
                </span>
              </label>

              {/* Clean override — only when backing was auto-picked and a clean stem exists */}
              {offerInstrumentalChoice && (
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="final-instrumental"
                    className="h-4 w-4"
                    checked={decision === 'clean'}
                    onChange={selectClean}
                  />
                  <span>{tPreview('audioInstrumentalClean')}</span>
                </label>
              )}

              {/* Advanced — opt into the full instrumental review screen */}
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="final-instrumental"
                  className="h-4 w-4"
                  checked={decision === 'advanced'}
                  onChange={selectAdvanced}
                />
                <span>
                  {autoInstrumentalSelection === 'with_backing'
                    ? t('advancedMode')
                    : t('advancedModeClean')}
                </span>
              </label>
            </div>

            {/* Context for the current decision */}
            {decision === 'auto' && autoInstrumentalSelection === 'with_backing' && (
              <>
                <p className="text-muted-foreground">{t('autoInstrumentalBacking')}</p>
                {showBackingWaveform && getWaveformData && (
                  <BackingVocalsWaveform
                    getWaveformData={getWaveformData}
                    currentTime={previewTime}
                    onSeek={(time) => previewRef.current?.auditionInstrumental('with_backing', time)}
                  />
                )}
              </>
            )}
            {decision === 'auto' && autoInstrumentalSelection !== 'with_backing' && (
              <p className="text-muted-foreground">{t('autoInstrumentalClean')}</p>
            )}
            {decision === 'clean' && (
              <p className="text-muted-foreground">{t('autoInstrumentalCleanChosen')}</p>
            )}
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
