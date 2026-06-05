/**
 * @jest-environment jsdom
 *
 * Tests for useDurationConfirm hook and getDurationConfirmData helper.
 *
 * We test the pure helper (getDurationConfirmData) directly and test the hook
 * via renderHook + act from @testing-library/react.
 */

import { renderHook, act } from "@testing-library/react"
import { getDurationConfirmData, useDurationConfirm } from "../use-duration-confirm"
import type { Job } from "@/lib/api"

// ---------------------------------------------------------------------------
// Mock api.confirmDuration
// ---------------------------------------------------------------------------
jest.mock("@/lib/api", () => ({
  api: {
    confirmDuration: jest.fn(),
  },
}))

import { api } from "@/lib/api"
const mockConfirmDuration = api.confirmDuration as jest.MockedFunction<typeof api.confirmDuration>

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeJob(overrides: Partial<Job> = {}): Job {
  return {
    job_id: "job-abc",
    status: "awaiting_duration_confirm",
    progress: 0,
    created_at: "2026-06-05T00:00:00Z",
    updated_at: "2026-06-05T00:00:00Z",
    state_data: {
      credits_charged: 1,
      pending_additional_credits: 2,
      duration_actual_seconds: 1500,
      duration_confirm_reason: "reconcile",
    },
    ...overrides,
  } as Job
}

// ---------------------------------------------------------------------------
// getDurationConfirmData unit tests
// ---------------------------------------------------------------------------

describe("getDurationConfirmData", () => {
  it("returns null when status is not awaiting_duration_confirm", () => {
    const job = makeJob({ status: "pending" })
    expect(getDurationConfirmData(job)).toBeNull()
  })

  it("computes totalCredits = credits_charged + pending_additional_credits", () => {
    const job = makeJob({
      state_data: {
        credits_charged: 1,
        pending_additional_credits: 2,
        duration_actual_seconds: 1500,
        duration_confirm_reason: "reconcile",
      },
    })
    const data = getDurationConfirmData(job)
    expect(data).not.toBeNull()
    expect(data!.totalCredits).toBe(3)
    expect(data!.acknowledgedCredits).toBe(3)
  })

  it("sets durationSeconds from duration_actual_seconds", () => {
    const job = makeJob({
      state_data: {
        credits_charged: 1,
        pending_additional_credits: 2,
        duration_actual_seconds: 1500,
        duration_confirm_reason: "reconcile",
      },
    })
    const data = getDurationConfirmData(job)
    expect(data!.durationSeconds).toBe(1500)
  })

  it("sets durationSeconds to null when duration_actual_seconds is absent", () => {
    const job = makeJob({
      state_data: {
        credits_charged: 1,
        pending_additional_credits: 2,
      },
    })
    const data = getDurationConfirmData(job)
    expect(data!.durationSeconds).toBeNull()
  })

  it("sets reconcile=true when duration_confirm_reason is 'reconcile'", () => {
    const job = makeJob({
      state_data: {
        credits_charged: 1,
        pending_additional_credits: 2,
        duration_actual_seconds: 1500,
        duration_confirm_reason: "reconcile",
      },
    })
    const data = getDurationConfirmData(job)
    expect(data!.reconcile).toBe(true)
  })

  it("sets reconcile=false when duration_confirm_reason is 'preflight'", () => {
    const job = makeJob({
      state_data: {
        credits_charged: 1,
        pending_additional_credits: 2,
        duration_actual_seconds: 905,
        duration_confirm_reason: "preflight",
      },
    })
    const data = getDurationConfirmData(job)
    expect(data!.reconcile).toBe(false)
  })

  it("handles missing state_data gracefully (zeros)", () => {
    const job = makeJob({ state_data: undefined })
    const data = getDurationConfirmData(job)
    expect(data).not.toBeNull()
    expect(data!.totalCredits).toBe(0)
    expect(data!.durationSeconds).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// useDurationConfirm hook tests
// ---------------------------------------------------------------------------

describe("useDurationConfirm", () => {
  const onConfirmSuccess = jest.fn()
  const onNeedRefresh = jest.fn()

  beforeEach(() => {
    jest.clearAllMocks()
  })

  function renderTestHook() {
    return renderHook(() =>
      useDurationConfirm({ onConfirmSuccess, onNeedRefresh })
    )
  }

  it("starts in idle phase", () => {
    const { result } = renderTestHook()
    expect(result.current.modalState.phase).toBe("idle")
  })

  it("openForJob transitions to open phase with correct data", () => {
    const job = makeJob()
    const { result } = renderTestHook()

    act(() => {
      result.current.openForJob(job)
    })

    expect(result.current.modalState.phase).toBe("open")
    if (result.current.modalState.phase === "open") {
      expect(result.current.modalState.data.totalCredits).toBe(3)
      expect(result.current.modalState.data.acknowledgedCredits).toBe(3)
      expect(result.current.modalState.data.durationSeconds).toBe(1500)
      expect(result.current.modalState.data.reconcile).toBe(true)
      expect(result.current.modalState.jobId).toBe("job-abc")
    }
  })

  it("openForJob does nothing for non-awaiting_duration_confirm jobs", () => {
    const job = makeJob({ status: "pending" })
    const { result } = renderTestHook()

    act(() => {
      result.current.openForJob(job)
    })

    expect(result.current.modalState.phase).toBe("idle")
  })

  it("handleClose transitions back to idle", () => {
    const job = makeJob()
    const { result } = renderTestHook()

    act(() => { result.current.openForJob(job) })
    act(() => { result.current.handleClose() })

    expect(result.current.modalState.phase).toBe("idle")
  })

  it("handleConfirm calls api.confirmDuration with acknowledgedCredits and calls onConfirmSuccess", async () => {
    const updatedJob = makeJob({ status: "pending" })
    mockConfirmDuration.mockResolvedValueOnce(updatedJob)

    const job = makeJob()
    const { result } = renderTestHook()

    act(() => { result.current.openForJob(job) })
    await act(async () => { await result.current.handleConfirm() })

    expect(mockConfirmDuration).toHaveBeenCalledWith("job-abc", 3)
    expect(onConfirmSuccess).toHaveBeenCalledWith(updatedJob)
    expect(result.current.modalState.phase).toBe("idle")
  })

  it("handleConfirm on 402 transitions to buy_credits phase", async () => {
    const err402 = Object.assign(new Error("Insufficient credits"), { status: 402 })
    mockConfirmDuration.mockRejectedValueOnce(err402)

    const job = makeJob()
    const { result } = renderTestHook()

    act(() => { result.current.openForJob(job) })
    await act(async () => { await result.current.handleConfirm() })

    expect(result.current.modalState.phase).toBe("buy_credits")
    expect(onConfirmSuccess).not.toHaveBeenCalled()
  })

  it("handleConfirm on 409 closes modal and calls onNeedRefresh", async () => {
    const err409 = Object.assign(new Error("Figure changed"), { status: 409 })
    mockConfirmDuration.mockRejectedValueOnce(err409)

    const job = makeJob()
    const { result } = renderTestHook()

    act(() => { result.current.openForJob(job) })
    await act(async () => { await result.current.handleConfirm() })

    expect(result.current.modalState.phase).toBe("idle")
    expect(onNeedRefresh).toHaveBeenCalledTimes(1)
    expect(onConfirmSuccess).not.toHaveBeenCalled()
  })

  it("handleBuyCredits transitions to buy_credits phase when in open state", () => {
    const job = makeJob()
    const { result } = renderTestHook()

    act(() => { result.current.openForJob(job) })
    act(() => { result.current.handleBuyCredits() })

    expect(result.current.modalState.phase).toBe("buy_credits")
  })

  it("handleBuyCreditsClose returns to open phase and calls onNeedRefresh", () => {
    const job = makeJob()
    const { result } = renderTestHook()

    act(() => { result.current.openForJob(job) })
    act(() => { result.current.handleBuyCredits() })
    act(() => { result.current.handleBuyCreditsClose() })

    expect(result.current.modalState.phase).toBe("open")
    expect(onNeedRefresh).toHaveBeenCalledTimes(1)
  })
})
