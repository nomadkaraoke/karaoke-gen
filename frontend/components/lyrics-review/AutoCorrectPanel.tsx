'use client'

import { useState } from 'react'
import { useTranslations } from 'next-intl'
import { Card } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import {
  Sparkles,
  Check,
  X,
  Undo2,
  ChevronDown,
  ChevronUp,
  AlertTriangle,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import type { AiSuggestion } from '@/lib/api/autoCorrect'
import type { AutoCorrectController } from '@/hooks/useAutoCorrect'

interface AutoCorrectPanelProps {
  controller: AutoCorrectController
  isReadOnly: boolean
}

function scrollToWord(wordId: string | undefined) {
  if (!wordId) return
  const el = document.getElementById(`word-${wordId}`)
  if (el) {
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
    el.classList.add('animate-lyrics-flash')
    setTimeout(() => el.classList.remove('animate-lyrics-flash'), 1300)
  }
}

const CATEGORY_BADGE: Record<AiSuggestion['category'], string> = {
  mishearing: 'bg-purple-500/15 text-purple-600 dark:text-purple-400',
  grammar: 'bg-blue-500/15 text-blue-600 dark:text-blue-400',
  adlib_removal: 'bg-orange-500/15 text-orange-600 dark:text-orange-400',
  repeated_chorus_fix: 'bg-teal-500/15 text-teal-600 dark:text-teal-400',
  formatting: 'bg-slate-500/15 text-slate-600 dark:text-slate-400',
  other: 'bg-slate-500/15 text-slate-600 dark:text-slate-400',
}

export default function AutoCorrectPanel({
  controller,
  isReadOnly,
}: AutoCorrectPanelProps) {
  const t = useTranslations('lyricsReview.autoCorrect')
  const [collapsed, setCollapsed] = useState(false)
  const {
    suggestions,
    decisions,
    model,
    pendingCount,
    acceptedCount,
    accept,
    reject,
    undoAccept,
    acceptAll,
    rejectAll,
    dismiss,
    isPendingAndStale,
  } = controller

  if (controller.status !== 'reviewing') return null

  return (
    <Card className="mb-3 border-purple-500/40">
      <div className="flex items-center justify-between gap-2 px-3 py-2">
        <div className="flex items-center gap-2 min-w-0">
          <Sparkles className="h-4 w-4 shrink-0 text-purple-500" />
          <span className="text-sm font-medium truncate">
            {suggestions.length === 0
              ? t('noSuggestions')
              : t('summary', {
                  total: suggestions.length,
                  pending: pendingCount,
                  accepted: acceptedCount,
                })}
          </span>
          <Badge variant="outline" className="hidden sm:inline-flex text-xs">
            {model}
          </Badge>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {!isReadOnly && pendingCount > 0 && (
            <>
              <Button size="sm" variant="outline" onClick={acceptAll}>
                <Check className="h-3.5 w-3.5 mr-1" />
                {t('acceptAll')}
              </Button>
              <Button size="sm" variant="outline" onClick={rejectAll}>
                <X className="h-3.5 w-3.5 mr-1" />
                {t('rejectAll')}
              </Button>
            </>
          )}
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setCollapsed((c) => !c)}
            aria-label={collapsed ? t('expand') : t('collapse')}
          >
            {collapsed ? (
              <ChevronDown className="h-4 w-4" />
            ) : (
              <ChevronUp className="h-4 w-4" />
            )}
          </Button>
          <Button size="sm" variant="ghost" onClick={dismiss} aria-label={t('dismiss')}>
            <X className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {!collapsed && suggestions.length > 0 && (
        <ScrollArea className="max-h-64 border-t">
          <ul className="divide-y">
            {suggestions.map((s) => {
              const decision = decisions[s.id]
              const stale = decision === 'stale' || isPendingAndStale(s)
              return (
                <li
                  key={s.id}
                  role="button"
                  tabIndex={0}
                  className={cn(
                    'flex items-center gap-2 px-3 py-1.5 text-sm cursor-pointer hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:outline-none',
                    decision === 'rejected' && 'opacity-50',
                  )}
                  onClick={() => scrollToWord(s.word_ids[0])}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      scrollToWord(s.word_ids[0])
                    }
                  }}
                >
                  <Badge
                    className={cn('shrink-0 text-[10px]', CATEGORY_BADGE[s.category])}
                  >
                    {t(`category_${s.category}`)}
                  </Badge>
                  {s.total_models > 1 && (
                    <TooltipProvider>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Badge
                            variant="outline"
                            className={cn(
                              'shrink-0 text-[10px] tabular-nums',
                              s.consensus === s.total_models
                                ? 'border-green-500/50 text-green-600 dark:text-green-400'
                                : 'border-amber-500/50 text-amber-600 dark:text-amber-400',
                            )}
                          >
                            {t('consensusBadge', {
                              consensus: s.consensus,
                              total: s.total_models,
                            })}
                          </Badge>
                        </TooltipTrigger>
                        <TooltipContent className="max-w-xs">
                          {t('consensusTooltip', { models: s.models.join(', ') })}
                        </TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                  )}
                  {s.conflict_group && (
                    <TooltipProvider>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Badge
                            variant="outline"
                            className="shrink-0 text-[10px] border-purple-500/50 text-purple-600 dark:text-purple-400"
                          >
                            {t('conflictBadge')}
                          </Badge>
                        </TooltipTrigger>
                        <TooltipContent className="max-w-xs">
                          {t('conflictTooltip')}
                        </TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                  )}
                  <span className="min-w-0 flex-1 truncate">
                    {s.op === 'insert_after' ? (
                      <>
                        <span className="text-muted-foreground">{t('insertMarker')} </span>
                        <span className="text-green-600 dark:text-green-400">
                          {s.new_text}
                        </span>
                      </>
                    ) : s.op === 'delete' ? (
                      <span className="line-through text-red-600 dark:text-red-400">
                        {s.original_text}
                      </span>
                    ) : (
                      <>
                        <span className="line-through text-red-600 dark:text-red-400">
                          {s.original_text}
                        </span>{' '}
                        <span className="text-green-600 dark:text-green-400">
                          {s.new_text}
                        </span>
                      </>
                    )}
                  </span>
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
                          {Math.round(s.confidence * 100)}%
                        </span>
                      </TooltipTrigger>
                      <TooltipContent className="max-w-xs">{s.reason}</TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                  <div
                    className="flex items-center gap-1 shrink-0"
                    onClick={(e) => e.stopPropagation()}
                  >
                    {stale ? (
                      <TooltipProvider>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <AlertTriangle className="h-4 w-4 text-amber-500" />
                          </TooltipTrigger>
                          <TooltipContent>{t('staleHint')}</TooltipContent>
                        </Tooltip>
                      </TooltipProvider>
                    ) : decision === 'pending' && !isReadOnly ? (
                      <>
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-7 px-2 text-green-600 hover:text-green-700"
                          onClick={() => accept(s.id)}
                          aria-label={t('accept')}
                        >
                          <Check className="h-4 w-4" />
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-7 px-2 text-red-600 hover:text-red-700"
                          onClick={() => reject(s.id)}
                          aria-label={t('reject')}
                        >
                          <X className="h-4 w-4" />
                        </Button>
                      </>
                    ) : decision === 'accepted' ? (
                      <>
                        <Badge variant="outline" className="text-[10px] text-green-600">
                          {t('accepted')}
                        </Badge>
                        {!isReadOnly && (
                          <Button
                            size="sm"
                            variant="ghost"
                            className="h-7 px-2"
                            onClick={() => undoAccept(s.id)}
                            aria-label={t('undo')}
                          >
                            <Undo2 className="h-4 w-4" />
                          </Button>
                        )}
                      </>
                    ) : decision === 'rejected' ? (
                      <Badge variant="outline" className="text-[10px]">
                        {t('rejected')}
                      </Badge>
                    ) : null}
                  </div>
                </li>
              )
            })}
          </ul>
        </ScrollArea>
      )}
    </Card>
  )
}
