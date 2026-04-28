'use client'

import { useTranslations } from 'next-intl'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { RefreshCw, ClipboardPaste, TextCursorInput, CaseSensitive, Sparkles, X, type LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

interface ModeSelectionModalProps {
  open: boolean
  onClose: () => void
  onSelectReplace: () => void
  onSelectResync: () => void
  onSelectReplaceSegments: () => void
  onSelectChangeCase: () => void
  onSelectCustomLyrics: () => void
  hasExistingLyrics: boolean
}

type ModeOption = {
  key: string
  icon: LucideIcon
  title: string
  desc: string
  tag?: string
  tagTone?: 'positive' | 'caution'
  primary?: boolean
  onSelect: () => void
}

export default function ModeSelectionModal({
  open,
  onClose,
  onSelectReplace,
  onSelectResync,
  onSelectReplaceSegments,
  onSelectChangeCase,
  onSelectCustomLyrics,
  hasExistingLyrics,
}: ModeSelectionModalProps) {
  const t = useTranslations('lyricsReview.modals.modeSelection')
  const tCommon = useTranslations('common')

  const rawOptions: Array<ModeOption | false> = [
    hasExistingLyrics && {
      key: 'resync',
      icon: RefreshCw,
      title: t('resyncTitle'),
      desc: t('resyncDesc'),
      tag: t('resyncRecommended'),
      tagTone: 'positive' as const,
      primary: true,
      onSelect: onSelectResync,
    },
    hasExistingLyrics && {
      key: 'replaceSegments',
      icon: TextCursorInput,
      title: t('replaceSegmentTitle'),
      desc: t('replaceSegmentDesc'),
      tag: t('replaceSegmentRecommended'),
      tagTone: 'positive' as const,
      onSelect: onSelectReplaceSegments,
    },
    hasExistingLyrics && {
      key: 'changeCase',
      icon: CaseSensitive,
      title: t('changeCaseTitle'),
      desc: t('changeCaseDesc'),
      tag: t('changeCaseRecommended'),
      tagTone: 'positive' as const,
      onSelect: onSelectChangeCase,
    },
    hasExistingLyrics && {
      key: 'customLyrics',
      icon: Sparkles,
      title: t('customLyricsTitle'),
      desc: t('customLyricsDesc'),
      tag: t('customLyricsTag'),
      tagTone: 'positive' as const,
      onSelect: onSelectCustomLyrics,
    },
    {
      key: 'replaceAll',
      icon: ClipboardPaste,
      title: t('replaceAllTitle'),
      desc: t('replaceAllDesc'),
      tag: t('replaceAllWarning'),
      tagTone: 'caution' as const,
      onSelect: onSelectReplace,
    },
  ]
  const options: ModeOption[] = rawOptions.filter((opt): opt is ModeOption => Boolean(opt))

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center justify-between">
            <span>{t('title')}</span>
            <Button variant="ghost" size="icon" onClick={onClose} className="h-8 w-8">
              <X className="h-4 w-4" />
            </Button>
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">{t('chooseMethod')}</p>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {options.map(({ key, icon: Icon, title, desc, tag, tagTone, primary, onSelect }) => (
              <button
                key={key}
                type="button"
                onClick={onSelect}
                className={cn(
                  'p-3 rounded-lg border text-left transition-colors',
                  'flex gap-3 items-start',
                  'focus:outline-none focus-visible:ring-2 focus-visible:ring-primary',
                  primary
                    ? 'border-2 border-primary hover:bg-primary/10'
                    : 'hover:bg-muted/30 hover:border-muted-foreground'
                )}
              >
                <Icon
                  className={cn(
                    'h-7 w-7 shrink-0 mt-0.5',
                    primary ? 'text-primary' : 'text-muted-foreground'
                  )}
                />
                <div className="flex-1 min-w-0">
                  <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
                    <h3
                      className={cn(
                        'text-sm font-semibold leading-tight',
                        primary && 'text-primary'
                      )}
                    >
                      {title}
                    </h3>
                    {tag && (
                      <span
                        className={cn(
                          'text-[11px] font-medium leading-tight',
                          tagTone === 'caution' ? 'text-yellow-500' : 'text-green-500'
                        )}
                      >
                        {tag}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-muted-foreground mt-1 line-clamp-2">{desc}</p>
                </div>
              </button>
            ))}
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            {tCommon('cancel')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
