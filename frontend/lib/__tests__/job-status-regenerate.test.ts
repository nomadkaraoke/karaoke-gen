import { canRegenerate, getJobStep, isRegenerating } from "../job-status"
import type { Job } from "../api"

const job = (overrides: Partial<Job>): Job =>
  ({ job_id: "j", status: "complete", progress: 100, created_at: "", updated_at: "", ...overrides }) as Job

describe("storage retention status helpers", () => {
  it("offers Regenerate for archived renders", () => {
    expect(canRegenerate(job({ renders_purged_at: "2026-09-01T00:00:00Z" }))).toBe(true)
  })

  it("offers Regenerate when a made track is missing its 4K file", () => {
    expect(canRegenerate(job({ file_urls: { finals: { lossy_720p_mp4: "x" } } }))).toBe(true)
  })

  it("does not offer it for complete finals, unfinished, deleted or finalise-only tracks", () => {
    const finals = { finals: { lossy_720p_mp4: "x", lossy_4k_mp4: "y" } }
    expect(canRegenerate(job({ file_urls: finals }))).toBe(false)
    expect(canRegenerate(job({ status: "encoding", renders_purged_at: "x" }))).toBe(false)
    expect(canRegenerate(job({ renders_purged_at: "x", outputs_deleted_at: "y" }))).toBe(false)
    expect(canRegenerate(job({ renders_purged_at: "x", finalise_only: true }))).toBe(false)
    expect(canRegenerate(job({ file_urls: {} }))).toBe(false)
  })

  it("isRegenerating follows the marker until completion", () => {
    expect(isRegenerating(job({ status: "rendering_video", state_data: { regenerate: {} } }))).toBe(true)
    expect(isRegenerating(job({ status: "complete", state_data: { regenerate: {} } }))).toBe(false)
    expect(isRegenerating(job({ status: "rendering_video", state_data: {} }))).toBe(false)
  })

  it("labels a running stems restore", () => {
    const step = getJobStep(job({ status: "lyrics_complete", state_data: { stems_restore: { status: "running" } } }))
    expect(step.label).toBe("restoringStems")
  })
})
