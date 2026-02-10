"use client"

import { useState } from "react"
import { Job, api } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { useToast } from "@/hooks/use-toast"
import {
  Loader2, RotateCcw, XCircle
} from "lucide-react"

interface JobActionsProps {
  job: Job
  onRefresh: () => void
}

export function JobActions({ job, onRefresh }: JobActionsProps) {
  const [isRetrying, setIsRetrying] = useState(false)
  const [isCancelling, setIsCancelling] = useState(false)
  const { toast } = useToast()

  const canRetry = job.status === "failed" || job.status === "cancelled"
  const canCancel = !["complete", "failed", "cancelled"].includes(job.status)

  async function handleRetry() {
    setIsRetrying(true)
    try {
      await api.retryJob(job.job_id)
      toast({
        title: "Job retry started",
        description: "The job is being retried. Refreshing status...",
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
        title: "Retry failed",
        description: errorMessage,
        variant: "destructive",
      })
    } finally {
      setIsRetrying(false)
    }
  }

  async function handleCancel() {
    if (!confirm("Are you sure you want to cancel this job?")) return

    setIsCancelling(true)
    try {
      await api.cancelJob(job.job_id)
      onRefresh()
    } catch (error) {
      console.error("Failed to cancel job:", error)
    } finally {
      setIsCancelling(false)
    }
  }

  // Don't render anything if no actions are available
  if (!canCancel && !canRetry) {
    return null
  }

  return (
    <div className="flex flex-wrap gap-2" onClick={(e) => e.stopPropagation()}>
      {/* Cancel button (red) */}
      {canCancel && (
        <Button
          size="sm"
          variant="ghost"
          onClick={handleCancel}
          disabled={isCancelling}
          className="text-xs text-red-400 hover:text-red-300"
        >
          {isCancelling ? (
            <Loader2 className="w-3 h-3 mr-1 animate-spin" />
          ) : (
            <XCircle className="w-3 h-3 mr-1" />
          )}
          Cancel
        </Button>
      )}

      {/* Retry button */}
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
          Retry
        </Button>
      )}
    </div>
  )
}
