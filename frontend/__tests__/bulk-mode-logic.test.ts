import { shouldSelectTrack, canSubmitBatch } from "@/components/job/bulk/types"

describe("shouldSelectTrack", () => {
  it("selects a plain studio track by default", () => {
    expect(shouldSelectTrack({ is_extra: false, available: false })).toBe(true)
  })
  it("does NOT select an extra (live/remix/bonus) track", () => {
    expect(shouldSelectTrack({ is_extra: true, available: false })).toBe(false)
  })
  it("does NOT select a track that already has a community version", () => {
    expect(shouldSelectTrack({ is_extra: false, available: true })).toBe(false)
  })
  it("treats unknown availability as selectable", () => {
    expect(shouldSelectTrack({ is_extra: false, available: null })).toBe(true)
  })
})

describe("canSubmitBatch", () => {
  it("blocks when nothing selected", () => {
    expect(canSubmitBatch(0, 10, false, 100)).toBe(false)
  })
  it("blocks over the max", () => {
    expect(canSubmitBatch(101, 1000, false, 100)).toBe(false)
  })
  it("blocks when credits are short", () => {
    expect(canSubmitBatch(5, 4, false, 100)).toBe(false)
  })
  it("allows when credits cover the count", () => {
    expect(canSubmitBatch(5, 5, false, 100)).toBe(true)
  })
  it("admins bypass the credit gate", () => {
    expect(canSubmitBatch(50, 0, true, 100)).toBe(true)
  })
  it("unknown balance does not block", () => {
    expect(canSubmitBatch(5, null, false, 100)).toBe(true)
  })
})
