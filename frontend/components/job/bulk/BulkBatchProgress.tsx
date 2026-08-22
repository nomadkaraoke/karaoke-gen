"use client"

import { useCallback, useEffect, useState } from "react"
import { useTranslations } from "next-intl"
import { api, BulkBatch, BulkBatchJob } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Loader2, Music2, CheckCircle2, AlertCircle } from "lucide-react"
import { AudioSearchDialog } from "@/components/audio-search/AudioSearchDialog"

interface BulkBatchProgressProps {
  batchId: string
  onStartAnother: () => void
  onJobsChanged?: () => void
}

export function BulkBatchProgress({ batchId, onStartAnother, onJobsChanged }: BulkBatchProgressProps) {
  const t = useTranslations("bulk")
  const [batch, setBatch] = useState<BulkBatch | null>(null)
  const [error, setError] = useState("")
  const [selectingJob, setSelectingJob] = useState<BulkBatchJob | null>(null)

  const load = useCallback(async () => {
    try {
      setBatch(await api.getBulkBatch(batchId))
    } catch {
      setError(t("progressError"))
    }
  }, [batchId, t])

  useEffect(() => {
    load()
    const interval = setInterval(load, 5000)
    return () => clearInterval(interval)
  }, [load])

  if (error) return <p className="text-sm" style={{ color: "#ef4444" }}>{error}</p>
  if (!batch) {
    return (
      <div className="flex items-center gap-2 text-sm" style={{ color: "var(--text-muted)" }}>
        <Loader2 className="w-4 h-4 animate-spin" />{t("loadingBatch")}
      </div>
    )
  }

  const c = batch.counts
  const awaiting = batch.jobs.filter((j) => j.status === "awaiting_audio_selection")

  return (
    <div className="space-y-4">
      <div className="rounded-lg border p-3" style={{ borderColor: "var(--card-border)" }}>
        <div className="flex items-center justify-between mb-2">
          <p className="text-sm font-medium" style={{ color: "var(--text)" }}>
            {t("batchProgressTitle", { total: batch.total })}
          </p>
          <Button variant="outline" size="sm" onClick={onStartAnother}>{t("startAnother")}</Button>
        </div>
        <div className="flex flex-wrap gap-2 text-xs">
          {c.searching > 0 && <Badge variant="outline" className="gap-1"><Loader2 className="w-3 h-3 animate-spin" />{t("countSearching", { count: c.searching })}</Badge>}
          {c.awaiting_selection > 0 && <Badge variant="outline" className="text-amber-500 border-amber-500/40">{t("countAwaiting", { count: c.awaiting_selection })}</Badge>}
          {c.processing > 0 && <Badge variant="outline">{t("countProcessing", { count: c.processing })}</Badge>}
          {c.complete > 0 && <Badge variant="outline" className="text-green-500 border-green-500/40 gap-1"><CheckCircle2 className="w-3 h-3" />{t("countComplete", { count: c.complete })}</Badge>}
          {c.failed > 0 && <Badge variant="outline" className="text-red-500 border-red-500/40 gap-1"><AlertCircle className="w-3 h-3" />{t("countFailed", { count: c.failed })}</Badge>}
        </div>
      </div>

      {awaiting.length > 0 && (
        <div className="rounded-lg border p-3 space-y-2" style={{ borderColor: "var(--card-border)" }}>
          <p className="text-sm font-medium" style={{ color: "var(--text)" }}>{t("reviewQueueTitle")}</p>
          <p className="text-xs" style={{ color: "var(--text-muted)" }}>{t("reviewQueueHint")}</p>
          {awaiting.map((j) => (
            <div key={j.job_id} className="flex items-center gap-2">
              <Music2 className="w-4 h-4 shrink-0" style={{ color: "var(--text-muted)" }} />
              <span className="flex-1 min-w-0 text-sm truncate" style={{ color: "var(--text)" }}>
                {j.artist} — {j.title}
              </span>
              <Button size="sm" onClick={() => setSelectingJob(j)}>{t("chooseAudio")}</Button>
            </div>
          ))}
        </div>
      )}

      {selectingJob && (
        <AudioSearchDialog
          jobId={selectingJob.job_id}
          open={true}
          searchArtist={selectingJob.artist}
          searchTitle={selectingJob.title}
          onClose={() => setSelectingJob(null)}
          onSelect={() => {
            setSelectingJob(null)
            load()
            onJobsChanged?.()
          }}
        />
      )}
    </div>
  )
}
