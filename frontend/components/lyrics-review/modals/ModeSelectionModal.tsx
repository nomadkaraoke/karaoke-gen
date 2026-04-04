'use client'

import { useTranslations } from 'next-intl'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { RefreshCw, ClipboardPaste, TextCursorInput, X } from 'lucide-react'
import { cn } from '@/lib/utils'

interface ModeSelectionModalProps {
  open: boolean
  onClose: () => void
  onSelectReplace: () => void
  onSelectResync: () => void
  onSelectReplaceSegments: () => void
  hasExistingLyrics: boolean
}

export default function ModeSelectionModal({
  open,
  onClose,
  onSelectReplace,
  onSelectResync,
  onSelectReplaceSegments,
  hasExistingLyrics,
}: ModeSelectionModalProps) {
  const t = useTranslations('lyricsReview.modals.modeSelection')
  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center justify-between">
            <span>{t('title')}</span>
            <Button variant="ghost" size="icon" onClick={onClose} className="h-8 w-8">
              <X className="h-4 w-4" />
            </Button>
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">{t('chooseMethod')}</p>

          <div className="flex flex-col gap-3">
            {/* Re-sync Existing option - only show if there are existing lyrics */}
            {hasExistingLyrics && (
              <div
                className={cn(
                  'p-4 border-2 border-primary rounded-lg cursor-pointer',
                  'hover:bg-primary/10 transition-colors'
                )}
                onClick={onSelectResync}
              >
                <div className="flex items-start gap-3">
                  <RefreshCw className="h-10 w-10 text-primary mt-0.5" />
                  <div className="flex-1">
                    <h3 className="text-lg font-semibold text-primary">{t('resyncTitle')}</h3>
                    <p className="text-sm text-muted-foreground mt-1">
                      {t('resyncDesc')}
                    </p>
                    <p className="text-xs text-green-500 mt-2">{t('resyncRecommended')}</p>
                  </div>
                </div>
              </div>
            )}

            {/* Replace Segment Lyrics option - only show if there are existing lyrics */}
            {hasExistingLyrics && (
              <div
                className={cn(
                  'p-4 border rounded-lg cursor-pointer',
                  'hover:bg-muted/30 hover:border-muted-foreground transition-colors'
                )}
                onClick={onSelectReplaceSegments}
              >
                <div className="flex items-start gap-3">
                  <TextCursorInput className="h-10 w-10 text-muted-foreground mt-0.5" />
                  <div className="flex-1">
                    <h3 className="text-lg font-semibold">{t('replaceSegmentTitle')}</h3>
                    <p className="text-sm text-muted-foreground mt-1">
                      {t('replaceSegmentDesc')}
                    </p>
                    <p className="text-xs text-green-500 mt-2">{t('replaceSegmentRecommended')}</p>
                  </div>
                </div>
              </div>
            )}

            {/* Replace All option */}
            <div
              className={cn(
                'p-4 border rounded-lg cursor-pointer',
                'hover:bg-muted/30 hover:border-muted-foreground transition-colors'
              )}
              onClick={onSelectReplace}
            >
              <div className="flex items-start gap-3">
                <ClipboardPaste className="h-10 w-10 text-muted-foreground mt-0.5" />
                <div className="flex-1">
                  <h3 className="text-lg font-semibold">{t('replaceAllTitle')}</h3>
                  <p className="text-sm text-muted-foreground mt-1">
                    {t('replaceAllDesc')}
                  </p>
                  <p className="text-xs text-yellow-500 mt-2">
                    {t('replaceAllWarning')}
                  </p>
                </div>
              </div>
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
