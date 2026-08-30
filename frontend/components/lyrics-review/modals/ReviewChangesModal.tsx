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
  /** Show the inline instrumental chooser ("Your karaoke video will use:") — true
   *  whenever both a clean and a with-backing stem exist, so the reviewer picks the
   *  instrumental here and the heavy /instrumental screen becomes an opt-in ("Advanced
   *  mode"). Independent of scorer confidence. */
  offerInlineChoice?: boolean
  /** The auto-scorer was confident about the backing decision → the recommended
   *  option gets a green "✓ recommended" badge + the confident helper copy. When
   *  false the recommended option is a softer "suggested" default. */
  autoConfident?: boolean
  /** Which instrumental to badge + preselect ("clean" | "with_backing"). */
  recommendedSelection?: 'clean' | 'with_backing' | null
  /** The instrumental currently chosen for the produced video. */
  currentSelection?: 'clean' | 'with_backing'
  onSelectInstrumental?: (choice: 'clean' | 'with_backing') => void
  /** Whether the reviewer opted into the full instrumental screen ("Advanced mode"). */
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
  offerInlineChoice = false,
  autoConfident = false,
  recommendedSelection = null,
  currentSelection,
  onSelectInstrumental,
  reviewInstrumentalAnyway = false,
  onToggleReviewInstrumental,
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
  const hasBackingStem = !!instrumentalOptions?.some(
    (o) => o.id === 'with_backing' && o.audio_url
  )
  const hasCleanStem = !!instrumentalOptions?.some(
    (o) => o.id === 'clean' && o.audio_url
  )

  // Render the "Your karaoke video will use:" chooser when both stems exist (the
  // reviewer picks inline) or when a confident single-stem verdict still wants the
  // recommended-option + Advanced-mode framing.
  const showChoiceBlock = offerInlineChoice || autoConfident
  // The instrumental currently chosen for the produced video (ignoring Advanced).
  const selected: 'clean' | 'with_backing' =
    currentSelection ?? recommendedSelection ?? (hasBackingStem ? 'with_backing' : 'clean')

  // Only offer the backing waveform (which clicks through to the backing stem)
  // when that stem is playable and the reviewer is currently keeping backing.
  const showBackingWaveform =
    !reviewInstrumentalAnyway && selected === 'with_backing' && hasBackingStem && !!getWaveformData

  const chooseInstrumental = (choice: 'clean' | 'with_backing') => {
    onToggleReviewInstrumental?.(false)
    onSelectInstrumental?.(choice)
    previewRef.current?.auditionInstrumental(choice)
  }
  const chooseAdvanced = () => onToggleReviewInstrumental?.(true)

  // A firm "✓ recommended" badge next to the recommended option — only when the
  // scorer was confident. When it wasn't, the option is still preselected but
  // carries no badge (the neutral "have a listen and choose" prompt says it's the
  // reviewer's call), avoiding a subtle recommended-vs-suggested distinction that
  // doesn't survive translation.
  const recommendedBadge = (value: 'clean' | 'with_backing') => {
    if (!autoConfident || recommendedSelection !== value) return null
    return (
      <span className="text-xs text-green-600 dark:text-green-400 whitespace-nowrap">
        ✓ {t('recommended')}
      </span>
    )
  }

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
          autoSelection={selected}
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

        {/* "Your karaoke video will use:" — the explicit final-output choice,
            separate from the preview-audio toggle above (which only controls what
            you hear). Shown whenever both stems exist so the reviewer picks the
            instrumental here; the full /instrumental screen is the "Advanced mode"
            opt-in. When the scorer was confident the recommended option carries a
            green ✓; otherwise it's a softer preselected "suggested" default. */}
        {showChoiceBlock && (
          <div className="rounded-lg border border-purple-500/40 bg-purple-500/5 p-3 text-sm space-y-3">
            <p className="font-medium">🎬 {t('finalChoiceLabel')}</p>
            <div role="radiogroup" className="space-y-2">
              {hasBackingStem && (
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="final-instrumental"
                    className="h-4 w-4"
                    checked={!reviewInstrumentalAnyway && selected === 'with_backing'}
                    onChange={() => chooseInstrumental('with_backing')}
                  />
                  <span>{tPreview('audioInstrumentalBacking')}</span>
                  {recommendedBadge('with_backing')}
                </label>
              )}

              {hasCleanStem && (
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="radio"
                    name="final-instrumental"
                    className="h-4 w-4"
                    checked={!reviewInstrumentalAnyway && selected === 'clean'}
                    onChange={() => chooseInstrumental('clean')}
                  />
                  <span>{tPreview('audioInstrumentalClean')}</span>
                  {recommendedBadge('clean')}
                </label>
              )}

              {/* Advanced — opt into the full instrumental review screen */}
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="final-instrumental"
                  className="h-4 w-4"
                  checked={!!reviewInstrumentalAnyway}
                  onChange={chooseAdvanced}
                />
                <span>
                  {recommendedSelection === 'clean' ? t('advancedModeClean') : t('advancedMode')}
                </span>
              </label>
            </div>

            {/* Context for the current choice */}
            {!reviewInstrumentalAnyway && (
              <>
                {!autoConfident ? (
                  <p className="text-muted-foreground">{t('chooseInstrumentalPrompt')}</p>
                ) : selected === 'with_backing' ? (
                  <p className="text-muted-foreground">{t('autoInstrumentalBacking')}</p>
                ) : recommendedSelection === 'clean' ? (
                  <p className="text-muted-foreground">{t('autoInstrumentalClean')}</p>
                ) : (
                  <p className="text-muted-foreground">{t('autoInstrumentalCleanChosen')}</p>
                )}
                {showBackingWaveform && getWaveformData && (
                  <BackingVocalsWaveform
                    getWaveformData={getWaveformData}
                    currentTime={previewTime}
                    onSeek={(time) => previewRef.current?.auditionInstrumental('with_backing', time)}
                  />
                )}
              </>
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
