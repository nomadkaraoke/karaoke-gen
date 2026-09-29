"use client"

import { useState } from "react"
import { useTranslations } from "next-intl"
import { Job, api } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { useToast } from "@/hooks/use-toast"
import { Clock, Loader2, Music, RotateCcw } from "lucide-react"

/** error_details.code set by the audio-download worker when a torrent never started. */
export const STALLED_DOWNLOAD_CODE = "audio_download_stalled"

export function isStalledDownload(job: Pick<Job, "status" | "error_details">): boolean {
  return job.status === "failed" && job.error_details?.code === STALLED_DOWNLOAD_CODE
}

interface StalledDownloadNoticeProps {
  job: Job
  onRefresh: () => void
  /** Called after audio selection is reopened, to show the Select Audio dialog. */
  onChooseAudio: () => void
}

/**
 * Replaces the raw error for a failed job whose torrent download stalled (its
 * only seeder was offline). Offers "Keep trying" (retry with a 1-hour stall
 * budget) or "Choose different audio" (reopen selection from saved results).
 */
export function StalledDownloadNotice({ job, onRefresh, onChooseAudio }: StalledDownloadNoticeProps) {
  const t = useTranslations("stalledDownload")
  const { toast } = useToast()
  const [busy, setBusy] = useState<"retry" | "choose" | null>(null)

  const minutes = Number(job.error_details?.stall_minutes) || 20
  const alreadyExtended = !!job.error_details?.keep_trying

  async function run(kind: "retry" | "choose") {
    setBusy(kind)
    try {
      if (kind === "retry") {
        await api.retryJob(job.job_id, { keepTrying: true })
        toast({ title: t("keepTryingStarted"), description: t("keepTryingStartedDesc") })
      } else {
        await api.chooseDifferentAudio(job.job_id)
        onChooseAudio()
      }
      onRefresh()
    } catch (error: any) {
      toast({
        title: t("actionFailed"),
        description: error?.message || t("actionFailedDesc"),
        variant: "destructive",
      })
    } finally {
      setBusy(null)
    }
  }

  return (
    <div
      className="mt-2 text-xs bg-amber-500/10 text-amber-300 rounded p-2 space-y-2"
      data-testid="stalled-download-notice"
    >
      <div className="flex items-start gap-2">
        <Clock className="w-3.5 h-3.5 mt-0.5 shrink-0" />
        <div className="space-y-1">
          <p className="font-medium">{t("title", { minutes })}</p>
          <p style={{ color: "var(--text-muted)" }}>
            {alreadyExtended ? t("explanationExtended") : t("explanation")}
          </p>
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          onClick={() => run("retry")}
          disabled={busy !== null}
          className="text-xs h-7 px-3 bg-amber-500 hover:bg-amber-600 text-white"
        >
          {busy === "retry" ? (
            <Loader2 className="w-3 h-3 mr-1 animate-spin" />
          ) : (
            <RotateCcw className="w-3 h-3 mr-1" />
          )}
          {t("keepTrying")}
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={() => run("choose")}
          disabled={busy !== null}
          className="text-xs h-7 px-3"
        >
          {busy === "choose" ? (
            <Loader2 className="w-3 h-3 mr-1 animate-spin" />
          ) : (
            <Music className="w-3 h-3 mr-1" />
          )}
          {t("chooseDifferentAudio")}
        </Button>
      </div>
    </div>
  )
}
