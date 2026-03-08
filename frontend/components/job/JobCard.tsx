"use client"

import { useState } from "react"
import { Job } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Zap } from "lucide-react"
import { JobActions } from "./JobActions"
import { AdminJobActions } from "./AdminJobActions"
import { OutputLinks } from "./OutputLinks"
import { AudioSearchDialog } from "../audio-search/AudioSearchDialog"
import { getJobStep, formatStepIndicator, getJobProgressPercent } from "@/lib/job-status"
import { useAuth } from "@/lib/auth"

/**
 * StatusIndicator component - Shows step-based progress with visual indicator
 *
 * Displays: "[4/10] Processing..." with colored text and optional progress bar
 */
function StatusIndicator({ job }: { job: Job }) {
  const { step, total, label, isBlocking, color } = getJobStep(job)
  const formattedStatus = formatStepIndicator(step, total, label)

  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={color}>{formattedStatus}</span>
      {isBlocking && (
        <span className="text-amber-400 text-[10px] font-medium uppercase tracking-wide">
          Action needed
        </span>
      )}
    </span>
  )
}

/**
 * Progress bar component - Visual indicator of job progress
 */
function ProgressBar({ job }: { job: Job }) {
  const progressPercent = getJobProgressPercent(job)
  const { step, color } = getJobStep(job)

  // Don't show progress bar for terminal states
  if (step === 0 || job.status === "complete" || job.status === "prep_complete") {
    return null
  }

  // Map color class to background color for the bar
  const barColorMap: Record<string, string> = {
    "text-muted-foreground": "bg-muted-foreground",
    "text-blue-400": "bg-blue-400",
    "text-purple-400": "bg-purple-400",
    "text-teal-400": "bg-teal-400",
    "text-cyan-400": "bg-cyan-400",
    "text-amber-400": "bg-amber-400",
    "text-indigo-400": "bg-indigo-400",
    "text-pink-400": "bg-pink-400",
    "text-violet-400": "bg-violet-400",
    "text-green-400": "bg-green-400",
  }
  const barColor = barColorMap[color] || "bg-primary-500"

  return (
    <div className="w-full h-1 rounded-full mt-2 overflow-hidden" style={{ backgroundColor: 'var(--secondary)' }}>
      <div
        className={`h-full ${barColor} rounded-full transition-all duration-500 ease-out`}
        style={{ width: `${progressPercent}%` }}
      />
    </div>
  )
}

interface JobCardProps {
  job: Job
  onRefresh: () => void
  showAdminControls?: boolean
}

export function JobCard({ job, onRefresh, showAdminControls }: JobCardProps) {
  const [showAudioSearch, setShowAudioSearch] = useState(false)
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'

  const createdAt = new Date(job.created_at).toLocaleString()
  const isInteractive = job.status === "awaiting_review" ||
                        job.status === "in_review" ||
                        job.status === "awaiting_instrumental_selection" ||
                        job.status === "awaiting_audio_selection" ||
                        job.status === "awaiting_audio_edit"
  const isComplete = job.status === "complete"
  const isFailed = job.status === "failed"

  // Render primary action button based on status
  const renderPrimaryAction = () => {
    // For non-interactive jobs in interactive states, show auto-processing message
    if (job.non_interactive && isInteractive) {
      return (
        <div className="flex items-center gap-1 text-xs text-amber-400">
          <Zap className="w-3 h-3" />
          Will auto-{job.status === "awaiting_review" || job.status === "in_review" ? "accept" :
                     job.status === "awaiting_instrumental_selection" ? "select" :
                     "select first"}
        </div>
      )
    }

    if (job.status === "awaiting_review" || job.status === "in_review") {
      // Use <a> instead of Link for hash-based URLs to ensure hashchange event fires
      return (
        <a
          href={`/app/jobs#/${job.job_id}/review`}
          className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded bg-primary-500 hover:bg-primary-600 text-white"
        >
          Review Lyrics
        </a>
      )
    }

    if (job.status === "awaiting_audio_edit") {
      return (
        <a
          href={`/app/jobs#/${job.job_id}/audio-edit`}
          className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded bg-primary-500 hover:bg-primary-600 text-white"
        >
          Edit Audio
        </a>
      )
    }

    if (job.status === "awaiting_audio_selection") {
      return (
        <Button
          size="sm"
          onClick={() => setShowAudioSearch(true)}
          className="text-xs h-7 px-3 bg-primary-500 hover:bg-primary-600"
        >
          Select Audio
        </Button>
      )
    }

    if (job.status === "awaiting_instrumental_selection") {
      // Use <a> instead of Link for hash-based URLs to ensure hashchange event fires
      return (
        <a
          href={`/app/jobs#/${job.job_id}/instrumental`}
          className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded bg-primary-500 hover:bg-primary-600 text-white"
        >
          Select Instrumental
        </a>
      )
    }

    return null
  }

  return (
    <div
      className={`rounded-lg border p-3 transition-colors overflow-hidden
        ${isComplete ? "border-green-500/30" : ""}
        ${isFailed ? "border-red-500/30" : ""}`}
      style={{
        borderColor: isComplete || isFailed ? undefined : 'var(--card-border)',
        backgroundColor: 'var(--card)',
      }}
    >
      {/* Header row with title and non-interactive badge */}
      <div className="flex items-start justify-between gap-2">
        <p className="font-medium truncate min-w-0" style={{ color: 'var(--text)' }}>
          {job.state_data?.brand_code && `${job.state_data.brand_code}: `}
          {job.artist || "Unknown"} - {job.title || "Unknown"}
        </p>
        {job.non_interactive && (
          <span className="shrink-0 inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-400 font-medium">
            <Zap className="w-2.5 h-2.5" />
            Auto
          </span>
        )}
      </div>

      {/* Meta row: ID, date, status - wraps on mobile */}
      <p className="text-xs mt-1 flex flex-wrap gap-x-1.5 gap-y-0.5" style={{ color: 'var(--text-muted)' }}>
        <span><span style={{ opacity: 0.7 }}>ID:</span> {job.job_id.slice(0, 8)}</span>
        <span style={{ opacity: 0.7 }}>•</span>
        <span>{createdAt}</span>
        <span style={{ opacity: 0.7 }}>•</span>
        <StatusIndicator job={job} />
      </p>

      {/* Progress bar for active jobs */}
      <ProgressBar job={job} />

      {/* Error message if any */}
      {job.error_message && (
        <div className="mt-2 text-xs text-red-400 bg-red-500/10 rounded p-2 break-words">
          {job.error_message}
        </div>
      )}

      {/* Output links for completed jobs, or admin link for admins on any job */}
      {(isComplete || isAdmin) && (
        <div className="mt-2">
          <OutputLinks job={job} onJobUpdated={onRefresh} />
        </div>
      )}

      {/* Actions row: Delete/secondary on left, primary action on right */}
      <div className="mt-2 flex items-center justify-between">
        <JobActions job={job} onRefresh={onRefresh} />
        {renderPrimaryAction()}
      </div>

      {/* Admin controls (toggled via header button) */}
      {showAdminControls && (
        <AdminJobActions job={job} onRefresh={onRefresh} />
      )}

      <AudioSearchDialog
        jobId={job.job_id}
        open={showAudioSearch}
        onClose={() => setShowAudioSearch(false)}
        onSelect={onRefresh}
        searchTitle={job.title}
      />
    </div>
  )
}
