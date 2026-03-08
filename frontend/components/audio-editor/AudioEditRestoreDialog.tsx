"use client"

import { useState, useEffect } from "react"
import { api, AudioEditSessionMeta, AudioEditSessionWithData, AudioEditEntry } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Spinner } from "@/components/ui/spinner"
import { X, History, ChevronRight } from "lucide-react"

interface AudioEditRestoreDialogProps {
  jobId: string
  onRestore: (entries: AudioEditEntry[]) => void
  onStartFresh: () => void
}

function formatDate(dateStr: string): string {
  const d = new Date(dateStr)
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  })
}

function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds)) return "0:00"
  const mins = Math.floor(Math.abs(seconds) / 60)
  const secs = Math.floor(Math.abs(seconds) % 60)
  const sign = seconds < 0 ? "-" : "+"
  return `${sign}${mins}:${secs.toString().padStart(2, "0")}`
}

export function AudioEditRestoreDialog({
  jobId,
  onRestore,
  onStartFresh,
}: AudioEditRestoreDialogProps) {
  const [sessions, setSessions] = useState<AudioEditSessionMeta[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null)
  const [selectedSession, setSelectedSession] = useState<AudioEditSessionWithData | null>(null)
  const [isLoadingDetail, setIsLoadingDetail] = useState(false)
  const [isRestoring, setIsRestoring] = useState(false)

  // Load sessions list
  useEffect(() => {
    async function loadSessions() {
      try {
        const { sessions } = await api.listAudioEditSessions(jobId)
        setSessions(sessions)
        if (sessions.length > 0) {
          setSelectedSessionId(sessions[0].session_id)
        }
      } catch (err) {
        console.warn("Failed to load audio edit sessions:", err)
      } finally {
        setIsLoading(false)
      }
    }
    loadSessions()
  }, [jobId])

  // Load selected session detail
  useEffect(() => {
    if (!selectedSessionId) return
    async function loadDetail() {
      setIsLoadingDetail(true)
      try {
        const session = await api.getAudioEditSession(jobId, selectedSessionId!)
        setSelectedSession(session)
      } catch (err) {
        console.warn("Failed to load session detail:", err)
      } finally {
        setIsLoadingDetail(false)
      }
    }
    loadDetail()
  }, [jobId, selectedSessionId])

  async function handleRestore() {
    if (!selectedSession?.edit_data?.entries) return
    setIsRestoring(true)
    onRestore(selectedSession.edit_data.entries)
  }

  if (isLoading) {
    return (
      <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
        <div
          className="rounded-lg border p-6 max-w-lg w-full text-center"
          style={{ borderColor: "var(--card-border)", backgroundColor: "var(--card)" }}
        >
          <Spinner className="w-6 h-6 mx-auto mb-2" />
          <p className="text-sm text-muted-foreground">Checking for saved sessions...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
      <div
        className="rounded-lg border max-w-2xl w-full flex flex-col max-h-[80vh]"
        style={{ borderColor: "var(--card-border)", backgroundColor: "var(--card)" }}
      >
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b" style={{ borderColor: "var(--card-border)" }}>
          <div className="flex items-center gap-2">
            <History className="w-5 h-5 text-blue-400" />
            <h2 className="text-lg font-semibold" style={{ color: "var(--text)" }}>
              Saved Audio Edit Sessions
            </h2>
          </div>
          <button onClick={onStartFresh} className="text-muted-foreground hover:text-foreground">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content */}
        <div className="flex flex-1 min-h-0">
          {/* Session list */}
          <div
            className="w-1/3 border-r overflow-y-auto p-2 space-y-1"
            style={{ borderColor: "var(--card-border)" }}
          >
            {sessions.map((session, i) => (
              <button
                key={session.session_id}
                onClick={() => setSelectedSessionId(session.session_id)}
                className={`w-full text-left rounded p-2 text-xs transition-colors ${
                  selectedSessionId === session.session_id
                    ? "bg-primary/10 border border-primary/30"
                    : "hover:bg-secondary/50"
                }`}
              >
                <div className="flex items-center gap-1">
                  <ChevronRight className="w-3 h-3 shrink-0" />
                  <div className="min-w-0">
                    <p className="font-medium truncate" style={{ color: "var(--text)" }}>
                      {formatDate(session.created_at)}
                    </p>
                    <p className="text-muted-foreground">
                      {session.edit_count} edit{session.edit_count !== 1 ? "s" : ""} · {session.trigger}
                      {i === 0 && (
                        <span className="ml-1 text-blue-400">[Latest]</span>
                      )}
                    </p>
                  </div>
                </div>
              </button>
            ))}
          </div>

          {/* Session detail */}
          <div className="flex-1 p-4 overflow-y-auto">
            {isLoadingDetail ? (
              <div className="flex items-center justify-center py-8">
                <Spinner className="w-5 h-5" />
              </div>
            ) : selectedSession?.edit_data?.entries ? (
              <div className="space-y-3">
                <p className="text-sm font-medium" style={{ color: "var(--text)" }}>
                  Operations applied:
                </p>
                <div className="space-y-1.5">
                  {selectedSession.edit_data.entries.map((entry: AudioEditEntry, i: number) => (
                    <div key={entry.edit_id || i} className="flex items-center gap-2 text-xs">
                      <span className="text-muted-foreground w-4 text-right">{i + 1}.</span>
                      <span className="font-medium capitalize" style={{ color: "var(--text)" }}>
                        {entry.operation.replace(/_/g, " ")}
                      </span>
                      <span className="text-muted-foreground">
                        {Math.floor(entry.duration_before / 60)}:{String(Math.floor(entry.duration_before % 60)).padStart(2, "0")}
                        {" → "}
                        {Math.floor(entry.duration_after / 60)}:{String(Math.floor(entry.duration_after % 60)).padStart(2, "0")}
                      </span>
                    </div>
                  ))}
                </div>
                {selectedSession.summary && (
                  <div className="text-xs text-muted-foreground pt-2 border-t" style={{ borderColor: "var(--card-border)" }}>
                    <p>
                      Duration change: {formatDuration(selectedSession.summary.duration_change_seconds)}
                    </p>
                    <p>
                      Net duration: {Math.floor(selectedSession.summary.net_duration_seconds / 60)}:
                      {String(Math.floor(selectedSession.summary.net_duration_seconds % 60)).padStart(2, "0")}
                    </p>
                  </div>
                )}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">Select a session to preview</p>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 p-4 border-t" style={{ borderColor: "var(--card-border)" }}>
          <Button variant="outline" onClick={onStartFresh} disabled={isRestoring}>
            Start Fresh
          </Button>
          <Button
            onClick={handleRestore}
            disabled={!selectedSession?.edit_data?.entries || isRestoring}
          >
            {isRestoring ? (
              <>
                <Spinner className="w-4 h-4 mr-1" />
                Restoring...
              </>
            ) : (
              "Restore Selected"
            )}
          </Button>
        </div>
      </div>
    </div>
  )
}
