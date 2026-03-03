"use client"

import { useState } from "react"
import { Job, api } from "@/lib/api"
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
  const [isRetrying, setIsRetrying] = useState(false)
  const [isDeleting, setIsDeleting] = useState(false)
  const { toast } = useToast()

  const canRetry = job.status === "failed"
  const canDelete = !["complete", "failed", "prep_complete"].includes(job.status)

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

  async function handleDelete() {
    if (!confirm("Are you sure you want to permanently cancel and delete this job? This cannot be undone.")) return

    setIsDeleting(true)
    try {
      await api.deleteJob(job.job_id)
      toast({
        title: "Job deleted",
        description: "The job has been permanently deleted.",
      })
      onRefresh()
    } catch (error: any) {
      console.error("Failed to delete job:", error)

      const errorMessage = error?.response?.data?.detail ||
                          error?.message ||
                          "Failed to delete job. Please try again."

      toast({
        title: "Delete failed",
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
          Delete
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
          Retry
        </Button>
      )}
    </div>
  )
}
