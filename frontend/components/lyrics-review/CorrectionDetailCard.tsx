'use client'

import { useTranslations } from 'next-intl'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Progress } from '@/components/ui/progress'
import { X, ArrowRight } from 'lucide-react'
import { cn } from '@/lib/utils'

interface CorrectionDetailCardProps {
  open: boolean
  onClose: () => void
  originalWord: string
  correctedWord: string
  category: string | null
  confidence: number
  reason: string
  handler: string
  source: string
  onRevert: () => void
  onEdit: () => void
  onAccept: () => void
}

// Format category name for display
const formatCategory = (category: string | null): string => {
  if (!category) return 'Unknown'
  return category
    .split('_')
    .map((word) => word.charAt(0) + word.slice(1).toLowerCase())
    .join(' ')
}

// Get emoji/icon for category
const getCategoryIcon = (category: string | null): string => {
  if (!category) return ''
  const icons: Record<string, string> = {
    SOUND_ALIKE: '',
    PUNCTUATION_ONLY: '',
    BACKGROUND_VOCALS: '',
    EXTRA_WORDS: '',
    REPEATED_SECTION: '',
    COMPLEX_MULTI_ERROR: '',
    AMBIGUOUS: '',
    NO_ERROR: '',
  }
  return icons[category] || ''
}

// Get confidence variant
const getConfidenceVariant = (confidence: number): 'destructive' | 'secondary' | 'default' => {
  if (confidence < 0.6) return 'destructive'
  if (confidence < 0.8) return 'secondary'
  return 'default'
}

export default function CorrectionDetailCard({
  open,
  onClose,
  originalWord,
  correctedWord,
  category,
  confidence,
  reason,
  handler,
  source,
  onRevert,
  onEdit,
  onAccept,
}: CorrectionDetailCardProps) {
  const t = useTranslations('lyricsReview.correctionDetail')
  const isMobile = typeof window !== 'undefined' && window.innerWidth < 640

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <DialogContent className={cn('max-w-md', isMobile && 'max-h-[85vh]')}>
        <DialogHeader>
          <DialogTitle className="flex justify-between items-center">
            <span className="text-lg font-semibold">{t('title')}</span>
            <Button variant="ghost" size="icon" onClick={onClose} className="h-8 w-8">
              <X className="h-4 w-4" />
            </Button>
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          {/* Original -> Corrected */}
          <div>
            <span className="text-xs text-muted-foreground mb-1 block">{t('change')}</span>
            <div className="flex items-center gap-3">
              <div className="flex-1 px-3 py-2 bg-red-100 dark:bg-red-900/30 rounded text-center line-through">
                <span className="font-medium">{originalWord}</span>
              </div>
              <ArrowRight className="h-4 w-4 text-muted-foreground" />
              <div className="flex-1 px-3 py-2 bg-green-100 dark:bg-green-900/30 rounded text-center">
                <span className="font-semibold">{correctedWord}</span>
              </div>
            </div>
          </div>

          {/* Category */}
          {category && (
            <div>
              <span className="text-xs text-muted-foreground mb-1 block">{t('category')}</span>
              <Badge variant="outline" className="text-sm">
                {getCategoryIcon(category)} {formatCategory(category)}
              </Badge>
            </div>
          )}

          {/* Confidence */}
          <div>
            <div className="flex justify-between mb-1">
              <span className="text-xs text-muted-foreground">{t('confidence')}</span>
              <span className="text-xs font-semibold">{(confidence * 100).toFixed(0)}%</span>
            </div>
            <Progress value={confidence * 100} className="h-2" />
          </div>

          {/* Reasoning */}
          <div>
            <span className="text-xs text-muted-foreground mb-1 block">{t('reasoning')}</span>
            <div className="p-3 bg-muted rounded border text-sm leading-relaxed">{reason}</div>
          </div>

          {/* Metadata */}
          <div className="flex flex-wrap gap-2">
            <Badge variant="outline" className="text-xs">
              {t('handler', { handler })}
            </Badge>
            <Badge variant="outline" className="text-xs">
              {t('source', { source })}
            </Badge>
          </div>
        </div>

        <DialogFooter className={cn('gap-2', isMobile && 'flex-col')}>
          <Button
            variant="outline"
            onClick={() => {
              onRevert()
              onClose()
            }}
            className={cn('text-destructive', isMobile && 'w-full h-11')}
          >
            {t('revertToOriginal')}
          </Button>
          <Button
            variant="outline"
            onClick={() => {
              onEdit()
              onClose()
            }}
            className={cn(isMobile && 'w-full h-11')}
          >
            {t('editCorrection')}
          </Button>
          <Button
            onClick={() => {
              onAccept()
              onClose()
            }}
            className={cn('bg-green-600 hover:bg-green-700', isMobile && 'w-full h-11')}
          >
            {t('markAsCorrect')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
