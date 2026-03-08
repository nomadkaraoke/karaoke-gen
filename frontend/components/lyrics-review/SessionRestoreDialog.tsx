'use client'

import { useState, useEffect, useCallback } from 'react'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Loader2, Clock, Search, AlertTriangle } from 'lucide-react'
import type { ReviewSession, ReviewSessionWithData, LyricsReviewApiClient } from '@/lib/api'
import type { CorrectionData } from '@/lib/lyrics-review/types'
import { cn } from '@/lib/utils'

interface SessionRestoreDialogProps {
  open: boolean
  onClose: () => void
  onRestore: (data: CorrectionData) => void
  sessions: ReviewSession[]
  apiClient: LyricsReviewApiClient
  currentAudioDuration?: number | null
  isLoading?: boolean
}

function formatRelativeTime(dateStr: string | null): string {
  if (!dateStr) return 'Unknown'
  const date = new Date(dateStr)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMin = Math.floor(diffMs / 60000)
  const diffHr = Math.floor(diffMin / 60)
  const diffDay = Math.floor(diffHr / 24)

  if (diffMin < 1) return 'Just now'
  if (diffMin < 60) return `${diffMin}m ago`
  if (diffHr < 24) return `${diffHr}h ago`
  if (diffDay < 7) return `${diffDay}d ago`
  return date.toLocaleDateString()
}

function formatAbsoluteTime(dateStr: string | null): string {
  if (!dateStr) return ''
  return new Date(dateStr).toLocaleString()
}

function TriggerBadge({ trigger }: { trigger: string }) {
  const colors: Record<string, string> = {
    auto: 'bg-gray-100 text-gray-600',
    preview: 'bg-blue-100 text-blue-700',
    manual: 'bg-green-100 text-green-700',
  }
  return (
    <span className={cn('px-1.5 py-0.5 rounded text-xs font-medium', colors[trigger] || colors.auto)}>
      {trigger}
    </span>
  )
}

function EditPreview({ session }: { session: ReviewSession }) {
  const { summary } = session
  const changedWords = summary?.changed_words || []

  if (changedWords.length === 0) {
    return (
      <div className="text-sm text-muted-foreground italic p-4">
        No word-level changes recorded in this session.
      </div>
    )
  }

  return (
    <div className="space-y-2 p-2 overflow-y-auto max-h-[400px]">
      <div className="text-xs text-muted-foreground mb-2">
        {summary.corrections_made} change{summary.corrections_made !== 1 ? 's' : ''} across {summary.total_segments} segments
      </div>
      {changedWords.map((change, i) => (
        <div key={i} className="flex items-start gap-2 text-sm py-1 border-b border-border/50 last:border-0">
          <span className="text-muted-foreground text-xs min-w-[24px] text-right">
            S{change.segment_index + 1}
          </span>
          <span className="text-red-500 line-through">{change.original}</span>
          <span className="text-muted-foreground">→</span>
          <span className="text-green-600 font-medium">{change.corrected}</span>
        </div>
      ))}
      {summary.corrections_made > changedWords.length && (
        <div className="text-xs text-muted-foreground pt-1">
          +{summary.corrections_made - changedWords.length} more changes
        </div>
      )}
    </div>
  )
}

export default function SessionRestoreDialog({
  open,
  onClose,
  onRestore,
  sessions,
  apiClient,
  currentAudioDuration,
  isLoading = false,
}: SessionRestoreDialogProps) {
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null)
  const [isRestoring, setIsRestoring] = useState(false)
  const [searchMode, setSearchMode] = useState<'this-job' | 'all-jobs'>('this-job')
  const [searchQuery, setSearchQuery] = useState('')
  const [crossJobSessions, setCrossJobSessions] = useState<ReviewSession[]>([])
  const [isSearching, setIsSearching] = useState(false)
  const [durationWarning, setDurationWarning] = useState<{ source: number; current: number } | null>(null)

  // Auto-select most recent session
  useEffect(() => {
    if (sessions.length > 0 && !selectedSessionId) {
      setSelectedSessionId(sessions[0].session_id)
    }
  }, [sessions, selectedSessionId])

  const displayedSessions = searchMode === 'this-job' ? sessions : crossJobSessions

  const selectedSession = displayedSessions.find(s => s.session_id === selectedSessionId) || null

  const handleSearch = useCallback(async () => {
    if (!searchQuery.trim()) return
    setIsSearching(true)
    try {
      // Use fetch directly for cross-job search (not part of job-scoped client)
      const { getAccessToken, API_BASE_URL } = await import('@/lib/api')
      const token = getAccessToken()
      const headers: HeadersInit = {}
      if (token) headers['Authorization'] = `Bearer ${token}`

      const response = await fetch(
        `${API_BASE_URL}/api/review/sessions/search?q=${encodeURIComponent(searchQuery)}&limit=20`,
        { headers }
      )
      if (response.ok) {
        const data = await response.json()
        setCrossJobSessions(data.sessions || [])
      }
    } catch (err) {
      console.error('Cross-job search failed:', err)
    } finally {
      setIsSearching(false)
    }
  }, [searchQuery])

  const handleRestore = useCallback(async () => {
    if (!selectedSession) return
    setIsRestoring(true)
    try {
      // Check duration mismatch for cross-job restores
      if (
        searchMode === 'all-jobs' &&
        currentAudioDuration &&
        selectedSession.audio_duration_seconds &&
        Math.abs(currentAudioDuration - selectedSession.audio_duration_seconds) > 2
      ) {
        setDurationWarning({
          source: selectedSession.audio_duration_seconds,
          current: currentAudioDuration,
        })
        setIsRestoring(false)
        return
      }

      await doRestore()
    } catch (err) {
      console.error('Failed to restore session:', err)
      setIsRestoring(false)
    }
  }, [selectedSession, searchMode, currentAudioDuration])

  const doRestore = useCallback(async () => {
    if (!selectedSession) return
    setIsRestoring(true)
    try {
      // For cross-job sessions, we need to fetch from that job's endpoint
      const jobId = selectedSession.job_id
      const sessionId = selectedSession.session_id

      let correctionData: CorrectionData | null = null

      if (searchMode === 'this-job') {
        const full = await apiClient.getReviewSession(sessionId)
        correctionData = full.correction_data
      } else {
        // Cross-job: use direct fetch
        const { getAccessToken, API_BASE_URL } = await import('@/lib/api')
        const token = getAccessToken()
        const headers: HeadersInit = {}
        if (token) headers['Authorization'] = `Bearer ${token}`

        const response = await fetch(
          `${API_BASE_URL}/api/review/${jobId}/sessions/${sessionId}`,
          { headers }
        )
        if (response.ok) {
          const data = await response.json()
          correctionData = data.correction_data
        }
      }

      if (correctionData) {
        onRestore(correctionData)
        onClose()
      }
    } catch (err) {
      console.error('Failed to restore session:', err)
    } finally {
      setIsRestoring(false)
      setDurationWarning(null)
    }
  }, [selectedSession, searchMode, apiClient, onRestore, onClose])

  const formatDuration = (seconds: number) => {
    const m = Math.floor(seconds / 60)
    const s = Math.round(seconds % 60)
    return `${m}:${s.toString().padStart(2, '0')}`
  }

  return (
    <>
      <Dialog open={open && !durationWarning} onOpenChange={(o) => !o && onClose()}>
        <DialogContent className="max-w-3xl max-h-[80vh] flex flex-col">
          <DialogHeader>
            <DialogTitle>Saved Review Sessions</DialogTitle>
          </DialogHeader>

          {/* Tab switcher */}
          <div className="flex gap-2 border-b pb-2">
            <Button
              variant={searchMode === 'this-job' ? 'default' : 'outline'}
              size="sm"
              onClick={() => setSearchMode('this-job')}
            >
              This Job ({sessions.length})
            </Button>
            <Button
              variant={searchMode === 'all-jobs' ? 'default' : 'outline'}
              size="sm"
              onClick={() => setSearchMode('all-jobs')}
            >
              All Jobs
            </Button>
          </div>

          {/* Cross-job search */}
          {searchMode === 'all-jobs' && (
            <div className="flex gap-2">
              <Input
                placeholder="Search by artist, title, or job ID..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
              />
              <Button onClick={handleSearch} disabled={isSearching} size="sm">
                {isSearching ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
              </Button>
            </div>
          )}

          {isLoading ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
            </div>
          ) : displayedSessions.length === 0 ? (
            <div className="text-center py-12 text-muted-foreground">
              {searchMode === 'all-jobs' ? 'Search for sessions across all jobs' : 'No saved sessions found'}
            </div>
          ) : (
            /* Split pane: session list (left) + preview (right) */
            <div className="flex gap-4 min-h-[300px] flex-1 overflow-hidden">
              {/* Session list */}
              <div className="w-1/2 overflow-y-auto border rounded-md">
                {displayedSessions.map((session) => (
                  <button
                    key={session.session_id}
                    onClick={() => setSelectedSessionId(session.session_id)}
                    className={cn(
                      'w-full text-left px-3 py-2.5 border-b last:border-0 hover:bg-accent/50 transition-colors',
                      selectedSessionId === session.session_id && 'bg-accent'
                    )}
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium" title={formatAbsoluteTime(session.updated_at)}>
                        <Clock className="h-3 w-3 inline mr-1 opacity-50" />
                        {formatRelativeTime(session.updated_at)}
                      </span>
                      <TriggerBadge trigger={session.trigger} />
                    </div>
                    <div className="text-xs text-muted-foreground mt-0.5">
                      {session.edit_count} edit{session.edit_count !== 1 ? 's' : ''}
                      {searchMode === 'all-jobs' && session.artist && (
                        <> &middot; {session.artist} - {session.title}</>
                      )}
                    </div>
                  </button>
                ))}
              </div>

              {/* Edit preview */}
              <div className="w-1/2 border rounded-md overflow-y-auto">
                {selectedSession ? (
                  <EditPreview session={selectedSession} />
                ) : (
                  <div className="text-sm text-muted-foreground p-4 italic">
                    Select a session to preview changes
                  </div>
                )}
              </div>
            </div>
          )}

          <DialogFooter>
            <Button variant="outline" onClick={onClose}>
              Start Fresh
            </Button>
            <Button
              onClick={handleRestore}
              disabled={!selectedSession || isRestoring}
            >
              {isRestoring && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
              Restore Selected
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Duration mismatch warning dialog */}
      <Dialog open={!!durationWarning} onOpenChange={() => setDurationWarning(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-yellow-500" />
              Duration Mismatch
            </DialogTitle>
          </DialogHeader>
          {durationWarning && (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                The selected session is from a track with a different duration:
              </p>
              <div className="bg-muted rounded-md p-3 text-sm space-y-1">
                <div>This job: <strong>{formatDuration(durationWarning.current)}</strong> ({Math.round(durationWarning.current)}s)</div>
                <div>Source session: <strong>{formatDuration(durationWarning.source)}</strong> ({Math.round(durationWarning.source)}s)</div>
              </div>
              <p className="text-sm text-muted-foreground">
                Timing alignment may not match. Word timestamps from the source session will be preserved as-is.
              </p>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setDurationWarning(null)}>
              Cancel
            </Button>
            <Button onClick={doRestore} disabled={isRestoring}>
              {isRestoring && <Loader2 className="h-4 w-4 animate-spin mr-2" />}
              Restore Anyway
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
