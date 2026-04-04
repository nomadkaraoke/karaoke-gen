"use client"

import { useState } from "react"
import { useTranslations } from 'next-intl'
import { Job, api } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { Button } from "@/components/ui/button"
import { useToast } from "@/hooks/use-toast"
import {
  Loader2, RotateCcw, Trash2
} from "lucide-react"

interface JobActionsProps {
  job: Job
  onRefresh: () => void
}

export function JobActions({ job, onRefresh }: JobActionsProps) {
  const t = useTranslations('jobActions')
  const [isRetrying, setIsRetrying] = useState(false)
  const [isDeleting, setIsDeleting] = useState(false)
  const { toast } = useToast()
  const { fetchUser } = useAuth()

  const canRetry = job.status === "failed"
  const canDelete = !["complete", "failed", "prep_complete"].includes(job.status)

  async function handleRetry() {
    setIsRetrying(true)
    try {
      await api.retryJob(job.job_id)
      toast({
        title: t('jobRetryStarted'),
        description: t('jobRetryStartedDesc'),
        variant: "default",
      })
      onRefresh()
    } catch (error: any) {
      console.error("Failed to retry job:", error)

      // Extract error message from API response
      const errorMessage = error?.response?.data?.detail ||
                          error?.message ||
                          "Failed to retry job. Please try again."

      toast({
        title: t('retryFailed'),
        description: errorMessage,
        variant: "destructive",
      })
    } finally {
      setIsRetrying(false)
    }
  }

  async function handleDelete() {
    if (!confirm(t('permanentlyCancel'))) return

    // Statuses where the backend does NOT refund credits
    const noRefundStatuses = ["complete", "prep_complete", "failed", "cancelled"]
    const willRefund = !noRefundStatuses.includes(job.status)

    setIsDeleting(true)
    try {
      await api.deleteJob(job.job_id)
      if (willRefund) {
        await fetchUser()
        const remaining = useAuth.getState().user?.credits ?? 0
        toast({
          title: t('creditRefunded'),
          description: t('creditRefundedDesc', { remaining }),
        })
      } else {
        toast({
          title: t('jobDeleted'),
          description: t('jobDeletedDesc'),
        })
      }
      onRefresh()
    } catch (error: any) {
      console.error("Failed to delete job:", error)

      const errorMessage = error?.response?.data?.detail ||
                          error?.message ||
                          "Failed to delete job. Please try again."

      toast({
        title: t('deleteFailed'),
        description: errorMessage,
        variant: "destructive",
      })
    } finally {
      setIsDeleting(false)
    }
  }

  // Don't render anything if no actions are available
  if (!canDelete && !canRetry) {
    return null
  }

  return (
    <div className="flex flex-wrap gap-2" onClick={(e) => e.stopPropagation()}>
      {/* Delete button (red) */}
      {canDelete && (
        <Button
          size="sm"
          variant="ghost"
          onClick={handleDelete}
          disabled={isDeleting}
          className="text-xs text-red-400 hover:text-red-300"
        >
          {isDeleting ? (
            <Loader2 className="w-3 h-3 mr-1 animate-spin" />
          ) : (
            <Trash2 className="w-3 h-3 mr-1" />
          )}
          {t('delete')}
        </Button>
      )}

      {/* Retry button (failed jobs only) */}
      {canRetry && (
        <Button
          size="sm"
          variant="ghost"
          onClick={handleRetry}
          disabled={isRetrying}
          className="text-xs text-blue-400 hover:text-blue-300"
        >
          {isRetrying ? (
            <Loader2 className="w-3 h-3 mr-1 animate-spin" />
          ) : (
            <RotateCcw className="w-3 h-3 mr-1" />
          )}
          {t('retry')}
        </Button>
      )}
    </div>
  )
}
