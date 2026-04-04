'use client'

import { useTranslations } from 'next-intl'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { LyricsSegment } from '@/lib/lyrics-review/types'

interface SegmentDetailsModalProps {
  open: boolean
  onClose: () => void
  segment: LyricsSegment | null
  segmentIndex: number | null
}

export default function SegmentDetailsModal({
  open,
  onClose,
  segment,
  segmentIndex,
}: SegmentDetailsModalProps) {
  const t = useTranslations('lyricsReview.modals.segmentDetails')
  if (!segment || segmentIndex === null) return null

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{t('title', { index: segmentIndex })}</DialogTitle>
        </DialogHeader>
        <pre className="m-0 font-mono text-sm whitespace-pre-wrap break-words overflow-auto max-h-[60vh] bg-muted p-4 rounded">
          {JSON.stringify(segment, null, 2)}
        </pre>
      </DialogContent>
    </Dialog>
  )
}
