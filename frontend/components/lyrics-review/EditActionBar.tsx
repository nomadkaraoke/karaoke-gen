'use client'

import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import { Trash2, RotateCcw, History } from 'lucide-react'
import { LyricsSegment } from '@/lib/lyrics-review/types'
import { cn } from '@/lib/utils'

interface EditActionBarProps {
  onReset: () => void
  onRevertToOriginal?: () => void
  onDelete?: () => void
  onClose: () => void
  onSave: () => void
  editedSegment: LyricsSegment | null
  originalTranscribedSegment?: LyricsSegment | null
  isGlobal?: boolean
}

export default function EditActionBar({
  onReset,
  onRevertToOriginal,
  onDelete,
  onClose,
  onSave,
  editedSegment,
  originalTranscribedSegment,
  isGlobal = false,
}: EditActionBarProps) {
  const t = useTranslations('lyricsReview.editActionBar')
  const isMobile = typeof window !== 'undefined' && window.innerWidth < 640

  return (
    <div
      className={cn(
        'flex w-full gap-2',
        isMobile ? 'flex-col items-stretch' : 'flex-row items-center'
      )}
    >
      <div
        className={cn(
          'flex items-center gap-2 flex-wrap',
          isMobile ? 'justify-center' : 'justify-start'
        )}
      >
        <Button variant="outline" size={isMobile ? 'sm' : 'default'} onClick={onReset}>
          <RotateCcw className="h-4 w-4 mr-1" />
          {t('reset')}
        </Button>
        {originalTranscribedSegment && (
          <Button variant="outline" size={isMobile ? 'sm' : 'default'} onClick={onRevertToOriginal}>
            <History className="h-4 w-4 mr-1" />
            {t('unCorrect')}
          </Button>
        )}
        {!isGlobal && onDelete && (
          <Button
            variant="outline"
            size={isMobile ? 'sm' : 'default'}
            onClick={onDelete}
            className="text-destructive hover:text-destructive"
          >
            <Trash2 className="h-4 w-4 mr-1" />
            {t('deleteSegment')}
          </Button>
        )}
      </div>
      <div
        className={cn('flex gap-2', isMobile ? 'justify-center ml-0' : 'justify-end ml-auto')}
      >
        <Button variant="outline" size={isMobile ? 'sm' : 'default'} onClick={onClose}>
          {t('cancel')}
        </Button>
        <Button
          size={isMobile ? 'sm' : 'default'}
          onClick={onSave}
          disabled={!editedSegment || editedSegment.words.length === 0}
        >
          {t('save')}
        </Button>
      </div>
    </div>
  )
}
