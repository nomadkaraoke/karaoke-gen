import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { TenantJobFlow } from "../TenantJobFlow"
import { api } from "@/lib/api"
import * as upload from "@/lib/upload"

jest.mock("@/lib/tenant", () => ({ useTenant: () => ({ branding: {} }) }))
jest.mock("@/lib/api", () => {
  const actual = jest.requireActual("@/lib/api")
  return { ...actual, api: { createJobFromUploadedAudio: jest.fn() } }
})
jest.mock("@/lib/upload", () => {
  const actual = jest.requireActual("@/lib/upload")
  return { ...actual, checkInstrumentalFile: jest.fn() }
})

const createJob = api.createJobFromUploadedAudio as jest.Mock
const check = upload.checkInstrumentalFile as jest.Mock

const mix = new File(["m"], "mix.wav", { type: "audio/wav" })
const inst = new File(["i"], "inst.wav", { type: "audio/wav" })

function fillForm() {
  fireEvent.change(screen.getByLabelText("Artist"), { target: { value: "Adele" } })
  fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Hello" } })
  fireEvent.change(document.getElementById("tenant-mixed-audio")!, { target: { files: [mix] } })
  fireEvent.change(document.getElementById("tenant-instrumental-audio")!, { target: { files: [inst] } })
}

describe("TenantJobFlow", () => {
  beforeEach(() => {
    createJob.mockReset()
    check.mockReset()
  })

  it("submits both files through the shared upload path and shows the progress modal", async () => {
    check.mockResolvedValue({ ok: true })
    let finish!: (v: any) => void
    createJob.mockImplementation((_m, _a, _t, _o, report) => {
      report({ phase: "uploading", loaded: 1, total: 2, fileName: "inst.wav", fileIndex: 2, fileCount: 2 })
      return new Promise((r) => { finish = r })
    })
    const onJobCreated = jest.fn()
    render(<TenantJobFlow onJobCreated={onJobCreated} />)
    fillForm()
    fireEvent.click(screen.getByRole("button", { name: /Submit Track/ }))

    expect(await screen.findByTestId("upload-progress-modal")).toBeInTheDocument()
    expect(screen.getByText("File 2 of 2: inst.wav")).toBeInTheDocument()
    expect(createJob).toHaveBeenCalledWith(mix, "Adele", "Hello", { is_private: true, instrumentalFile: inst }, expect.any(Function))

    await act(async () => { finish({ status: "success", job_id: "tenant-job-1", message: "ok" }) })
    await waitFor(() => expect(onJobCreated).toHaveBeenCalled())
    expect(screen.queryByTestId("upload-progress-modal")).not.toBeInTheDocument()
  })

  it("blocks a mismatched instrumental before uploading anything", async () => {
    check.mockResolvedValue({ ok: false, reason: "mismatch", fileSeconds: 170, expectedSeconds: 200 })
    render(<TenantJobFlow onJobCreated={jest.fn()} />)
    fillForm()
    fireEvent.click(screen.getByRole("button", { name: /Submit Track/ }))

    expect(await screen.findByText(/This instrumental is 2:50 long but your song is 3:20/)).toBeInTheDocument()
    expect(createJob).not.toHaveBeenCalled()
  })

  it("shows a connection error when the upload drops", async () => {
    check.mockResolvedValue({ ok: true })
    const { ApiError } = jest.requireActual("@/lib/api-error")
    createJob.mockRejectedValue(new ApiError("Upload failed: network error", 0))
    render(<TenantJobFlow onJobCreated={jest.fn()} />)
    fillForm()
    fireEvent.click(screen.getByRole("button", { name: /Submit Track/ }))

    expect(await screen.findByText(/Network error/)).toBeInTheDocument()
  })
})
