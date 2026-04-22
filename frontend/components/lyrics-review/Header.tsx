'use client'

import { useTranslations } from 'next-intl'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import {
  Lock,
  Upload,
  Search,
  Edit,
  Undo2,
  Redo2,
  Timer,
  Eye,
  CheckCircle2,
  XCircle,
} from 'lucide-react'
import { CorrectionData, InteractionMode } from '@/lib/lyrics-review/types'
import ModeSelector from './ModeSelector'
import AudioPlayer from './AudioPlayer'
import GuidancePanel from './GuidancePanel'
import { cn } from '@/lib/utils'

interface ApiClient {
  getAudioUrl: (hash: string) => string
}

interface HeaderProps {
  isReadOnly: boolean
  onFileLoad: () => void
  data: CorrectionData
  onMetricClick: {
    anchor: () => void
    corrected: () => void
    uncorrected: () => void
  }
  effectiveMode: InteractionMode
  onModeChange: (mode: InteractionMode) => void
  apiClient: ApiClient | null
  audioHash: string
  onTimeUpdate: (time: number) => void
  onHandlerToggle: (handler: string, enabled: boolean) => void
  isUpdatingHandlers: boolean
  onHandlerClick?: (handler: string) => void
  onFindReplace?: () => void
  onEditAll?: () => void
  onResetCorrections?: () => void
  onTimingOffset?: () => void
  timingOffsetMs?: number
  onUndo: () => void
  onRedo: () => void
  canUndo: boolean
  canRedo: boolean
  statsVisible: boolean
  onStatsToggle: () => void
  reviewMode?: boolean
  onReviewModeToggle?: (enabled: boolean) => void
  onAcceptAllCorrections?: () => void
  onAcceptHighConfidenceCorrections?: () => void
  onRevertAllCorrections?: () => void
  isDuet?: boolean
  onToggleDuet?: () => void
}

export default function Header({
  isReadOnly,
  onFileLoad,
  data,
  onMetricClick,
  effectiveMode,
  onModeChange,
  apiClient,
  audioHash,
  onTimeUpdate,
  onHandlerToggle,
  isUpdatingHandlers,
  onHandlerClick,
  onFindReplace,
  onEditAll,
  onResetCorrections,
  onTimingOffset,
  timingOffsetMs = 0,
  onUndo,
  onRedo,
  canUndo,
  canRedo,
  statsVisible,
  onStatsToggle,
  reviewMode = false,
  onReviewModeToggle,
  onAcceptAllCorrections,
  onAcceptHighConfidenceCorrections,
  onRevertAllCorrections,
  isDuet = false,
  onToggleDuet,
}: HeaderProps) {
  const t = useTranslations('lyricsReview.header')
  const tDuet = useTranslations('lyricsReview.duet')
  const isMobile = typeof window !== 'undefined' && window.innerWidth < 768

  // Check if agentic mode is active
  const availableHandlers = data.metadata.available_handlers || []
  const isAgenticMode = availableHandlers.some(
    (h: { id: string }) => h.id === 'AgenticCorrector'
  )

  return (
    <TooltipProvider>
      <div className="space-y-2">
        {/* Read-only indicator */}
        {isReadOnly && (
          <div className="flex items-center gap-1 text-muted-foreground text-sm mb-2">
            <Lock className="h-4 w-4" />
            <span>{t('viewOnlyMode')}</span>
          </div>
        )}

        {/* Review Mode toggle and Load File button */}
        {(!isReadOnly && isAgenticMode && onReviewModeToggle) || isReadOnly ? (
          <div className="flex gap-2 justify-end items-center mb-2">
            {!isReadOnly && isAgenticMode && onReviewModeToggle && (
              <Tooltip>
                <TooltipTrigger asChild>
                  <Badge
                    variant={reviewMode ? 'default' : 'outline'}
                    className="cursor-pointer hover:opacity-80 transition-opacity"
                    onClick={() => onReviewModeToggle(!reviewMode)}
                  >
                    <Eye className="h-3 w-3 mr-1" />
                    {reviewMode ? t('reviewMode') : t('reviewOff')}
                  </Badge>
                </TooltipTrigger>
                <TooltipContent>
                  {reviewMode
                    ? t('hideInlineActions')
                    : t('showInlineActions')}
                </TooltipContent>
              </Tooltip>
            )}
            {isReadOnly && (
              <Button variant="outline" size="sm" onClick={onFileLoad} className={cn(isMobile && 'w-full')}>
                <Upload className="h-4 w-4 mr-1" />
                {t('loadFile')}
              </Button>
            )}
          </div>
        ) : null}

        {/* Guidance panel with color legend, tips, and conditionally visible stats */}
        <GuidancePanel
          data={data}
          isReadOnly={isReadOnly}
          onMetricClick={onMetricClick}
          onHandlerToggle={onHandlerToggle}
          isUpdatingHandlers={isUpdatingHandlers}
          onHandlerClick={onHandlerClick}
          statsVisible={statsVisible}
          onStatsToggle={onStatsToggle}
        />

        {/* Batch Actions Panel */}
        {!isReadOnly && reviewMode && isAgenticMode && data.corrections?.length > 0 && (
          <Card className="p-2 mb-2 bg-accent/50">
            <div className={cn('flex gap-2', isMobile ? 'flex-col' : 'flex-row items-center justify-between')}>
              <span className="text-xs text-muted-foreground">
                {t('batchActions', { count: data.corrections.length })}
              </span>
              <div className="flex gap-2 flex-wrap">
                {onAcceptHighConfidenceCorrections && (
                  <Button
                    size="sm"
                    onClick={onAcceptHighConfidenceCorrections}
                    className="bg-green-600 hover:bg-green-700 text-xs h-7"
                  >
                    <CheckCircle2 className="h-3 w-3 mr-1" />
                    {t('acceptHighConfidence', { count: data.corrections.filter((c) => c.confidence >= 0.8).length })}
                  </Button>
                )}
                {onAcceptAllCorrections && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={onAcceptAllCorrections}
                    className="text-xs h-7"
                  >
                    <CheckCircle2 className="h-3 w-3 mr-1" />
                    {t('acceptAll')}
                  </Button>
                )}
                {onRevertAllCorrections && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={onRevertAllCorrections}
                    className="text-destructive text-xs h-7"
                  >
                    <XCircle className="h-3 w-3 mr-1" />
                    {t('revertAll')}
                  </Button>
                )}
              </div>
            </div>
          </Card>
        )}

        {/* Control bar */}
        <Card className="p-2 mb-2">
          <div
            className={cn(
              'flex gap-2 items-center',
              isMobile ? 'flex-col items-start' : 'flex-row'
            )}
          >
            <div className="flex gap-1 flex-wrap items-center flex-1">
              <ModeSelector effectiveMode={effectiveMode} onChange={onModeChange} />

              {!isReadOnly && onResetCorrections && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button variant="outline" size="sm" onClick={onResetCorrections} className="h-8 text-xs text-amber-500 border-amber-500/50 hover:border-amber-500 hover:bg-amber-500/10">
                      <Undo2 className="h-3.5 w-3.5 mr-1" />
                      {t('undoAll')}
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent className="max-w-xs">{t('resetAllChanges')}</TooltipContent>
                </Tooltip>
              )}

              {!isReadOnly && (
                <div className="flex h-8">
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="outline"
                        size="icon"
                        onClick={onUndo}
                        disabled={!canUndo}
                        className="h-8 w-8"
                      >
                        <Undo2 className="h-3.5 w-3.5" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>{t('undo')}</TooltipContent>
                  </Tooltip>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="outline"
                        size="icon"
                        onClick={onRedo}
                        disabled={!canRedo}
                        className="h-8 w-8"
                      >
                        <Redo2 className="h-3.5 w-3.5" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>{t('redo')}</TooltipContent>
                  </Tooltip>
                </div>
              )}

              {!isReadOnly && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button variant="outline" size="sm" onClick={onFindReplace} className="h-8 text-xs">
                      <Search className="h-3.5 w-3.5 mr-1" />
                      {t('findReplace')}
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent className="max-w-xs">{t('findReplaceDesc')}</TooltipContent>
                </Tooltip>
              )}

              {!isReadOnly && onEditAll && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button variant="outline" size="sm" onClick={onEditAll} className="h-8 text-xs">
                      <Edit className="h-3.5 w-3.5 mr-1" />
                      {t('editAll')}
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent className="max-w-xs">{t('editAllDesc')}</TooltipContent>
                </Tooltip>
              )}

              {!isReadOnly && onTimingOffset && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <div className="flex items-center">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={onTimingOffset}
                        className={cn('h-8 text-xs', timingOffsetMs !== 0 && 'border-purple-500 text-purple-500')}
                      >
                        <Timer className="h-3.5 w-3.5 mr-1" />
                        {t('timingOffset')}
                      </Button>
                      {timingOffsetMs !== 0 && (
                        <span className="ml-1 text-xs font-bold text-purple-500">
                          {timingOffsetMs > 0 ? '+' : ''}
                          {t('timingOffsetValue', { ms: timingOffsetMs })}
                        </span>
                      )}
                    </div>
                  </TooltipTrigger>
                  <TooltipContent className="max-w-xs">{t('timingOffsetDesc')}</TooltipContent>
                </Tooltip>
              )}

              {onToggleDuet && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={onToggleDuet}
                      className={cn(
                        'h-8 text-xs',
                        isDuet
                          ? 'bg-pink-900/40 border-pink-500 text-pink-100 hover:bg-pink-900/60'
                          : 'border-border text-foreground hover:bg-muted'
                      )}
                    >
                      ◐ {isDuet ? tDuet('duetOn') : tDuet('markAsDuet')}
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent className="max-w-xs">{tDuet('markAsDuetHint')}</TooltipContent>
                </Tooltip>
              )}

            </div>

            <AudioPlayer
              audioUrl={apiClient ? apiClient.getAudioUrl(audioHash) : null}
              onTimeUpdate={onTimeUpdate}
            />
          </div>
        </Card>
      </div>
    </TooltipProvider>
  )
}
