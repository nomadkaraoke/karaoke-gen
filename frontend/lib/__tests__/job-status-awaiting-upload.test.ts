import { getJobStep } from "../job-status"
import type { Job } from "../api"

describe("getJobStep awaiting upload", () => {
  it("labels a pending job whose browser upload hasn't finished as waitingForUpload", () => {
    const step = getJobStep({ status: "pending", state_data: { awaiting_upload: true } } as unknown as Job)
    expect(step.label).toBe("waitingForUpload")
    expect(step.step).toBe(1)
  })

  it("keeps settingUp for ordinary pending jobs", () => {
    expect(getJobStep({ status: "pending", state_data: {} } as unknown as Job).label).toBe("settingUp")
  })

  it("ignores a stale flag once the job has moved on", () => {
    const step = getJobStep({ status: "downloading", state_data: { awaiting_upload: true } } as unknown as Job)
    expect(step.label).not.toBe("waitingForUpload")
  })
})
