'use client'

import { useState, useEffect } from 'react'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { CorrectionData, InstrumentalOption, BackingVocalsAnalysis } from '@/lib/lyrics-review/types'
import { ArrowLeft, Upload, Loader2, AlertCircle } from 'lucide-react'
import PreviewVideoSection from '../PreviewVideoSection'
import { InstrumentalSelectorEmbedded } from '@/components/instrumental-review'
import type { InstrumentalSelectionType } from '@/lib/api'

interface ApiClient {
  generatePreviewVideo: (data: CorrectionData) => Promise<{
    status: string
    message?: string
    preview_hash?: string
  }>
  getPreviewVideoUrl: (hash: string) => string
}

interface ReviewChangesModalProps {
  open: boolean
  onClose: () => void
  data: CorrectionData
  onSubmit: (instrumentalSelection: InstrumentalSelectionType) => void
  isSubmitting?: boolean
  apiClient?: ApiClient | null
  timingOffsetMs?: number
}

export default function ReviewChangesModal({
  open,
  onClose,
  data,
  onSubmit,
  isSubmitting = false,
  apiClient = null,
  timingOffsetMs = 0,
}: ReviewChangesModalProps) {
  const corrections = data.corrections || []
  const totalSegments = data.corrected_segments?.length || 0

  // Check if there are manual corrections (user-made changes)
  const hasManualCorrections = corrections.some(c => c.handler === 'ManualCorrector' || c.handler === 'UserEdit')

  // Instrumental selection state
  const [instrumentalSelection, setInstrumentalSelection] = useState<InstrumentalSelectionType | null>(null)

  // Get instrumental options from correction data
  const instrumentalOptions = data.instrumental_options || []
  const backingVocalsAnalysis = data.backing_vocals_analysis || null
  const hasInstrumentalOptions = instrumentalOptions.length > 0

  // Set default selection based on recommendation when modal opens
  useEffect(() => {
    if (open && hasInstrumentalOptions && !instrumentalSelection) {
      const recommended = backingVocalsAnalysis?.recommended_selection
      if (recommended === 'clean' || recommended === 'with_backing') {
        setInstrumentalSelection(recommended as InstrumentalSelectionType)
      } else {
        // Default to with_backing if no recommendation
        setInstrumentalSelection('with_backing')
      }
    }
  }, [open, hasInstrumentalOptions, instrumentalSelection, backingVocalsAnalysis])

  // Convert instrumental options to the format expected by the embedded selector
  const selectorOptions = instrumentalOptions.map(opt => ({
    id: opt.id as InstrumentalSelectionType,
    label: opt.label,
    audio_url: opt.audio_url,
  }))

  // Validate submission
  const canSubmit = !hasInstrumentalOptions || instrumentalSelection !== null

  const handleSubmit = () => {
    if (!canSubmit) return
    // Pass the instrumental selection (or 'clean' as fallback if no options available)
    onSubmit(instrumentalSelection || 'clean')
  }

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Preview & Complete Review</DialogTitle>
        </DialogHeader>

        {/* Video Preview Section */}
        <PreviewVideoSection
          apiClient={apiClient}
          isModalOpen={open}
          updatedData={data}
          timingOffsetMs={timingOffsetMs}
        />

        {/* Instrumental Selection Section */}
        {hasInstrumentalOptions && (
          <div className="mt-4">
            <InstrumentalSelectorEmbedded
              options={selectorOptions}
              analysis={backingVocalsAnalysis ? {
                has_audible_content: backingVocalsAnalysis.has_audible_content,
                total_duration_seconds: backingVocalsAnalysis.total_duration_seconds,
                audible_segments: backingVocalsAnalysis.audible_segments,
                recommended_selection: backingVocalsAnalysis.recommended_selection,
                total_audible_duration_seconds: backingVocalsAnalysis.total_audible_duration_seconds,
                audible_percentage: backingVocalsAnalysis.audible_percentage,
                silence_threshold_db: backingVocalsAnalysis.silence_threshold_db,
              } : null}
              value={instrumentalSelection}
              onChange={setInstrumentalSelection}
              compact
              disabled={isSubmitting}
            />
          </div>
        )}

        {/* Info text */}
        <div className="text-sm text-muted-foreground space-y-1 mt-4">
          {hasManualCorrections ? (
            <p>Manual corrections detected. Review the preview to ensure the lyrics are synchronized correctly.</p>
          ) : (
            <p>No manual corrections detected. If everything looks good in the preview, select your instrumental and click submit.</p>
          )}
          <p>Total segments: {totalSegments}</p>
        </div>

        {/* Validation warning */}
        {hasInstrumentalOptions && !instrumentalSelection && (
          <div className="flex items-center gap-2 text-amber-600 text-sm mt-2">
            <AlertCircle className="h-4 w-4" />
            <span>Please select an instrumental track before completing the review.</span>
          </div>
        )}

        <DialogFooter className="border-t pt-4">
          <Button variant="ghost" onClick={onClose} disabled={isSubmitting} className="text-primary">
            <ArrowLeft className="h-4 w-4 mr-2" />
            Cancel
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={isSubmitting || !canSubmit}
            className="bg-green-600 hover:bg-green-700"
          >
            {isSubmitting ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Submitting...
              </>
            ) : (
              <>
                Complete Review
                <Upload className="h-4 w-4 ml-2" />
              </>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
