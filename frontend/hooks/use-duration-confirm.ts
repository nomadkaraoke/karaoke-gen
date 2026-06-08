"use client"

import { useState, useCallback } from "react"
import { api, type Job } from "@/lib/api"

/**
 * Derives the cost fields needed by DurationCostConfirm from a job's state_data.
 *
 * Returns null when the job is not in awaiting_duration_confirm (no-op path).
 */
export interface DurationConfirmData {
  /** Total credits that will be on account after confirm (credits_charged + pending_additional_credits) */
  totalCredits: number
  /** The amount passed to api.confirmDuration (same as totalCredits; validated server-side) */
  acknowledgedCredits: number
  /** Measured duration in seconds (may be null if not yet known) */
  durationSeconds: number | null
  /** Whether this is a reconcile (post-download cost increase) vs pre-flight */
  reconcile: boolean
}

export function getDurationConfirmData(job: Job): DurationConfirmData | null {
  if (job.status !== "awaiting_duration_confirm") return null
  const sd = job.state_data ?? {}
  const creditsCharged = typeof sd.credits_charged === "number" ? sd.credits_charged : 0
  const pendingAdditional =
    typeof sd.pending_additional_credits === "number" ? sd.pending_additional_credits : 0
  const totalCredits = creditsCharged + pendingAdditional
  const durationSeconds =
    typeof sd.duration_actual_seconds === "number" ? sd.duration_actual_seconds : null
  const reconcile = sd.duration_confirm_reason === "reconcile"

  return {
    totalCredits,
    acknowledgedCredits: totalCredits,
    durationSeconds,
    reconcile,
  }
}

export type DurationConfirmModalState =
  | { phase: "idle" }
  | { phase: "open"; jobId: string; data: DurationConfirmData }
  | { phase: "confirming"; jobId: string; data: DurationConfirmData }
  | { phase: "buy_credits"; jobId: string; data: DurationConfirmData }

interface UseDurationConfirmOptions {
  onConfirmSuccess: (updatedJob: Job) => void
  onNeedRefresh: () => void
}

/**
 * Hook that manages the DurationCostConfirm modal lifecycle for a single job card.
 *
 * - openForJob: call to open the modal for a job (pass the job object)
 * - handleConfirm: calls api.confirmDuration, handles 402/409 branches
 * - handleBuyCredits: switches to buy-credits phase
 * - handleClose: dismisses without confirming (job stays paused)
 * - modalState: current phase + data for the modal
 */
export function useDurationConfirm({ onConfirmSuccess, onNeedRefresh }: UseDurationConfirmOptions) {
  const [modalState, setModalState] = useState<DurationConfirmModalState>({ phase: "idle" })

  const openForJob = useCallback((job: Job) => {
    const data = getDurationConfirmData(job)
    if (!data) return
    setModalState({ phase: "open", jobId: job.job_id, data })
  }, [])

  const handleConfirm = useCallback(async () => {
    if (modalState.phase !== "open") return
    const { jobId, data } = modalState

    setModalState({ phase: "confirming", jobId, data })

    try {
      const updatedJob = await api.confirmDuration(jobId, data.acknowledgedCredits)
      setModalState({ phase: "idle" })
      onConfirmSuccess(updatedJob)
    } catch (err: unknown) {
      const apiErr = err as { status?: number }
      if (apiErr?.status === 402) {
        // Insufficient credits — switch to buy-credits phase
        setModalState({ phase: "buy_credits", jobId, data })
      } else if (apiErr?.status === 409) {
        // Figure changed — close modal and trigger a refresh so updated figures load
        setModalState({ phase: "idle" })
        onNeedRefresh()
      } else {
        // Unexpected error — close modal and refresh
        setModalState({ phase: "idle" })
        onNeedRefresh()
      }
    }
  }, [modalState, onConfirmSuccess, onNeedRefresh])

  const handleBuyCredits = useCallback(() => {
    if (modalState.phase === "open" || modalState.phase === "confirming") {
      setModalState({ phase: "buy_credits", jobId: modalState.jobId, data: modalState.data })
    }
  }, [modalState])

  const handleClose = useCallback(() => {
    setModalState({ phase: "idle" })
  }, [])

  const handleBuyCreditsClose = useCallback(() => {
    // After buying credits, close buy-credits dialog but re-open confirm modal
    // (user may now have enough credits to proceed)
    if (modalState.phase === "buy_credits") {
      const { jobId, data } = modalState
      onNeedRefresh() // Refresh user balance
      setModalState({ phase: "open", jobId, data })
    }
  }, [modalState, onNeedRefresh])

  return {
    modalState,
    openForJob,
    handleConfirm,
    handleBuyCredits,
    handleClose,
    handleBuyCreditsClose,
  }
}
