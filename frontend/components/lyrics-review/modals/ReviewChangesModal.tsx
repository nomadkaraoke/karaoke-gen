'use client'

import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { CorrectionData } from '@/lib/lyrics-review/types'
import { ArrowLeft, Upload, Loader2 } from 'lucide-react'
import PreviewVideoSection from '../PreviewVideoSection'

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
  onSubmit: () => void
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

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <DialogContent className="max-w-4xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Preview Video (With Vocals)</DialogTitle>
        </DialogHeader>

        {/* Video Preview Section */}
        <PreviewVideoSection
          apiClient={apiClient}
          isModalOpen={open}
          updatedData={data}
          timingOffsetMs={timingOffsetMs}
        />

        {/* Info text */}
        <div className="text-sm text-muted-foreground space-y-1">
          {hasManualCorrections ? (
            <p>Manual corrections detected. Review the preview to ensure the lyrics are synchronized correctly.</p>
          ) : (
            <p>No manual corrections detected. If everything looks good in the preview, click submit and the server will generate the final karaoke video.</p>
          )}
          <p>Total segments: {totalSegments}</p>
        </div>

        <DialogFooter className="border-t pt-4">
          <Button variant="ghost" onClick={onClose} disabled={isSubmitting} className="text-primary">
            <ArrowLeft className="h-4 w-4 mr-2" />
            Cancel
          </Button>
          <Button
            onClick={onSubmit}
            disabled={isSubmitting}
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
