"use client"

import { useEffect, useCallback, useRef } from "react"
import { api, Job } from "@/lib/api"

interface AutoProcessorProps {
  /** List of jobs to monitor */
  jobs: Job[]
  /** Callback to refresh jobs list after auto-processing */
  onJobsChanged: () => void
}

/**
 * AutoProcessor component that automatically handles interactive states
 * for jobs that have non_interactive mode enabled.
 *
 * This is equivalent to the -y flag in karaoke-gen-remote CLI:
 * - Auto-completes lyrics review (accepts auto-corrected lyrics)
 * - Auto-selects instrumental based on backing vocals analyzer recommendation
 * - Auto-selects first/best audio search result
 */
export function AutoProcessor({ jobs, onJobsChanged }: AutoProcessorProps) {
  const processedRef = useRef<Set<string>>(new Set())
  const processingRef = useRef<Set<string>>(new Set())

  const processJob = useCallback(async (job: Job, action: string) => {
    const processingKey = `${job.job_id}-${action}`

    // Skip if already being processed or already processed
    if (processingRef.current.has(processingKey) || processedRef.current.has(processingKey)) {
      return
    }

    // Mark as processing
    processingRef.current.add(processingKey)
    processedRef.current.add(processingKey)

    console.log(`[AutoProcessor] Auto-processing ${action} for job ${job.job_id}`)

    try {
      switch (action) {
        case 'review':
          // Auto-complete lyrics review
          await api.completeReview(job.job_id)
          console.log(`[AutoProcessor] Auto-completed review for job ${job.job_id}`)
          break

        case 'instrumental': {
          // Use backing vocals analyzer recommendation (default to clean if review needed or missing)
          const analysis = job.state_data?.backing_vocals_analysis
          const recommendation = analysis?.recommended_selection
          // Only use 'with_backing' if explicitly recommended by analyzer, otherwise clean (safe default)
          const selection = recommendation === 'with_backing' ? 'with_backing' : 'clean'
          await api.selectInstrumental(job.job_id, selection)
          console.log(`[AutoProcessor] Auto-selected ${selection} instrumental for job ${job.job_id} (analyzer recommendation: ${recommendation || 'none'})`)
          break
        }

        case 'audio':
          // Auto-select first audio search result (index 0)
          await api.selectAudioResult(job.job_id, 0)
          console.log(`[AutoProcessor] Auto-selected first audio result for job ${job.job_id}`)
          break
      }

      // Refresh jobs to reflect the change
      onJobsChanged()
    } catch (err) {
      console.error(`[AutoProcessor] Failed to auto-process ${action} for job ${job.job_id}:`, err)
      // Remove from processed so it can be retried
      processedRef.current.delete(processingKey)
    } finally {
      processingRef.current.delete(processingKey)
    }
  }, [onJobsChanged])

  useEffect(() => {
    // Find jobs that need auto-processing (only jobs with non_interactive flag)
    for (const job of jobs) {
      // Only process jobs with non_interactive mode enabled
      if (!job.non_interactive) {
        continue
      }

      switch (job.status) {
        case 'awaiting_review':
          processJob(job, 'review')
          break
        case 'awaiting_instrumental_selection':
          processJob(job, 'instrumental')
          break
        case 'awaiting_audio_selection':
          processJob(job, 'audio')
          break
      }
    }
  }, [jobs, processJob])

  // This component doesn't render anything - it's purely for side effects
  return null
}
