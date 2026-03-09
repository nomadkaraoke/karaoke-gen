"use client"

import { useState } from "react"
import { Job, adminApi } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { useToast } from "@/hooks/use-toast"
import {
  Loader2,
  RotateCcw,
  RefreshCw,
  Trash2,
  CloudOff,
  Music,
  Mic,
  Scissors,
} from "lucide-react"

interface AdminJobActionsProps {
  job: Job
  onRefresh: () => void
}

export function AdminJobActions({ job, onRefresh }: AdminJobActionsProps) {
  const [loading, setLoading] = useState<string | null>(null)
  const { toast } = useToast()

  async function handleAction(
    actionKey: string,
    action: () => Promise<void>,
    confirmMessage: string
  ) {
    if (!confirm(confirmMessage)) return
    setLoading(actionKey)
    try {
      await action()
      onRefresh()
    } catch (error: any) {
      toast({
        title: "Error",
        description: error?.message || `Failed to ${actionKey}`,
        variant: "destructive",
      })
    } finally {
      setLoading(null)
    }
  }

  const handleReset = (targetState: string, label: string) =>
    handleAction(`reset-${targetState}`, async () => {
      await adminApi.resetJob(job.job_id, targetState)
      toast({ title: "Job Reset", description: `Reset to ${label}` })
    }, `Reset this job to "${label}"?`)

  const handleDeleteOutputs = () =>
    handleAction("del-outputs", async () => {
      await adminApi.deleteJobOutputs(job.job_id)
      toast({ title: "Outputs Deleted", description: "Distribution files removed" })
    }, "Delete all output files (YouTube, Dropbox, GDrive)?")

  const handleRegenScreens = () =>
    handleAction("regen-screens", async () => {
      const result = await adminApi.regenerateScreens(job.job_id)
      if (result.worker_triggered) {
        toast({ title: "Regen Started", description: "Screens are being regenerated" })
      } else {
        toast({ title: "Regen Failed", description: result.error || result.message, variant: "destructive" })
      }
    }, "Regenerate title/end screens with current metadata?")

  const handleFullRestart = () =>
    handleAction("full-restart", async () => {
      const result = await adminApi.restartJob(job.job_id, {
        preserve_audio_stems: true,
        delete_outputs: true,
      })
      toast({ title: "Job Restarted", description: result.message })
    }, "Fully restart this job? (preserves audio stems)")

  const handleAudioSearch = () =>
    handleAction("audio-search", async () => {
      const result = await adminApi.overrideAudioSource(job.job_id, {
        source_type: "audio_search",
      })
      toast({ title: "Audio Source Changed", description: result.message })
    }, "Switch this job to audio search mode?")

  const handleDeleteJob = () =>
    handleAction("delete", async () => {
      await adminApi.deleteJob(job.job_id)
      toast({ title: "Job Deleted", description: "Job permanently deleted" })
    }, "Permanently delete this job and all files? This cannot be undone.")

  const isLoading = (key: string) => loading === key

  return (
    <div
      className="flex items-center gap-1 flex-wrap mt-2 pt-2 border-t"
      style={{ borderColor: 'var(--card-border)' }}
      onClick={(e) => e.stopPropagation()}
    >
      <span className="text-[10px] font-medium uppercase tracking-wide mr-0.5" style={{ color: 'var(--text-muted)' }}>
        Reset:
      </span>
      <Button variant="ghost" size="sm" className="h-6 px-1.5 text-[11px] text-blue-400 hover:text-blue-300"
        onClick={() => handleReset("awaiting_audio_selection", "Audio")}
        disabled={loading !== null}
      >
        {isLoading("reset-awaiting_audio_selection") ? <Loader2 className="w-3 h-3 animate-spin" /> : <Music className="w-3 h-3 mr-0.5" />}
        Audio
      </Button>
      <Button variant="ghost" size="sm" className="h-6 px-1.5 text-[11px] text-blue-400 hover:text-blue-300"
        onClick={() => handleReset("awaiting_audio_edit", "Audio Edit")}
        disabled={loading !== null}
      >
        {isLoading("reset-awaiting_audio_edit") ? <Loader2 className="w-3 h-3 animate-spin" /> : <Scissors className="w-3 h-3 mr-0.5" />}
        Audio Edit
      </Button>
      <Button variant="ghost" size="sm" className="h-6 px-1.5 text-[11px] text-blue-400 hover:text-blue-300"
        onClick={() => handleReset("awaiting_review", "Review")}
        disabled={loading !== null}
      >
        {isLoading("reset-awaiting_review") ? <Loader2 className="w-3 h-3 animate-spin" /> : <Mic className="w-3 h-3 mr-0.5" />}
        Review
      </Button>
      <Button variant="ghost" size="sm" className="h-6 px-1.5 text-[11px] text-blue-400 hover:text-blue-300"
        onClick={() => handleReset("instrumental_selected", "Reprocess")}
        disabled={loading !== null}
      >
        {isLoading("reset-instrumental_selected") ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3 mr-0.5" />}
        Reprocess
      </Button>

      <span className="text-muted-foreground mx-0.5">|</span>

      <Button variant="ghost" size="sm" className="h-6 px-1.5 text-[11px] text-orange-400 hover:text-orange-300"
        onClick={handleDeleteOutputs}
        disabled={loading !== null}
      >
        {isLoading("del-outputs") ? <Loader2 className="w-3 h-3 animate-spin" /> : <CloudOff className="w-3 h-3 mr-0.5" />}
        Del Outputs
      </Button>
      <Button variant="ghost" size="sm" className="h-6 px-1.5 text-[11px] text-blue-400 hover:text-blue-300"
        onClick={handleRegenScreens}
        disabled={loading !== null}
      >
        {isLoading("regen-screens") ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3 mr-0.5" />}
        Regen Screens
      </Button>
      <Button variant="ghost" size="sm" className="h-6 px-1.5 text-[11px] text-purple-400 hover:text-purple-300"
        onClick={handleFullRestart}
        disabled={loading !== null}
      >
        {isLoading("full-restart") ? <Loader2 className="w-3 h-3 animate-spin" /> : <RotateCcw className="w-3 h-3 mr-0.5" />}
        Full Restart
      </Button>
      <Button variant="ghost" size="sm" className="h-6 px-1.5 text-[11px] text-amber-400 hover:text-amber-300"
        onClick={handleAudioSearch}
        disabled={loading !== null}
      >
        {isLoading("audio-search") ? <Loader2 className="w-3 h-3 animate-spin" /> : <Music className="w-3 h-3 mr-0.5" />}
        Audio Search
      </Button>

      <span className="text-muted-foreground mx-0.5">|</span>

      <Button variant="ghost" size="sm" className="h-6 px-1.5 text-[11px] text-red-400 hover:text-red-300"
        onClick={handleDeleteJob}
        disabled={loading !== null}
      >
        {isLoading("delete") ? <Loader2 className="w-3 h-3 animate-spin" /> : <Trash2 className="w-3 h-3 mr-0.5" />}
        Delete
      </Button>
    </div>
  )
}
