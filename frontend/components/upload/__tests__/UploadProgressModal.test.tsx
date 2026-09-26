import { fireEvent, render, screen } from "@testing-library/react"
import { UploadProgressModal, estimateTransfer } from "../UploadProgressModal"

const MB = 1024 * 1024

describe("estimateTransfer", () => {
  it("returns null without enough history", () => {
    expect(estimateTransfer([], 100)).toBeNull()
    expect(estimateTransfer([{ at: 0, loaded: 0 }, { at: 500, loaded: 10 }], 100)).toBeNull()
  })

  it("computes speed and time left over the sample span", () => {
    const est = estimateTransfer([{ at: 0, loaded: 0 }, { at: 2000, loaded: 4 * MB }], 20 * MB)
    expect(est?.bytesPerSec).toBeCloseTo(2 * MB)
    expect(est?.secondsLeft).toBeCloseTo(8)
  })

  it("returns null when stalled", () => {
    expect(estimateTransfer([{ at: 0, loaded: 5 }, { at: 3000, loaded: 5 }], 100)).toBeNull()
  })
})

describe("UploadProgressModal", () => {
  afterEach(() => jest.restoreAllMocks())

  it("shows title, percent, sizes and the keep-tab-open warning while uploading", () => {
    render(<UploadProgressModal progress={{ phase: "uploading", loaded: 25 * MB, total: 100 * MB }} />)

    expect(screen.getByText("Uploading your file")).toBeInTheDocument()
    expect(screen.getByText("Uploading... 25%")).toBeInTheDocument()
    expect(screen.getByText(/25\.0 MB of 100\.0 MB/)).toBeInTheDocument()
    expect(screen.getByText("Estimating time left...")).toBeInTheDocument()
    expect(screen.getByText(/Keep this tab open until the upload finishes/)).toBeInTheDocument()
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "25")
  })

  it("shows speed and ETA once progress history accumulates", () => {
    const now = jest.spyOn(Date, "now")
    now.mockReturnValue(0)
    const { rerender } = render(
      <UploadProgressModal progress={{ phase: "uploading", loaded: 0, total: 100 * MB }} />
    )
    now.mockReturnValue(2000)
    rerender(<UploadProgressModal progress={{ phase: "uploading", loaded: 10 * MB, total: 100 * MB }} />)

    // 5 MB/s, 90 MB left → 18s
    expect(screen.getByText(/5\.0 MB\/s/)).toBeInTheDocument()
    expect(screen.getByText("About 18s left")).toBeInTheDocument()

    now.mockReturnValue(3000)
    rerender(<UploadProgressModal progress={{ phase: "uploading", loaded: 11 * MB, total: 1000 * MB }} />)
    // 11 MB in 3s → ~3.67 MB/s, 989 MB left → 270s
    expect(screen.getByText("About 4m 30s left")).toBeInTheDocument()
  })

  it("shows which file is uploading when there are several", () => {
    render(<UploadProgressModal progress={{ phase: "uploading", loaded: 1, total: 10, fileName: "inst.wav", fileIndex: 2, fileCount: 2 }} />)
    expect(screen.getByText("File 2 of 2: inst.wav")).toBeInTheDocument()
  })

  it("hides the file line for a single file and honours a custom finalizing label", () => {
    const { rerender } = render(<UploadProgressModal progress={{ phase: "uploading", loaded: 1, total: 10, fileName: "a.wav", fileIndex: 1, fileCount: 1 }} />)
    expect(screen.queryByText(/File 1 of 1/)).not.toBeInTheDocument()
    rerender(<UploadProgressModal progress={{ phase: "finalizing", loaded: 10, total: 10 }} finalizingLabel="Checking your instrumental..." />)
    expect(screen.getByText("Checking your instrumental...")).toBeInTheDocument()
  })

  it("cannot be dismissed with Escape", () => {
    render(<UploadProgressModal progress={{ phase: "uploading", loaded: 1, total: 100 }} />)
    fireEvent.keyDown(document.activeElement || document.body, { key: "Escape" })
    expect(screen.getByTestId("upload-progress-modal")).toBeInTheDocument()
    expect(screen.queryByText("Close")).not.toBeInTheDocument()
  })

  it("shows the creating and finalizing phases", () => {
    const { rerender } = render(<UploadProgressModal progress={{ phase: "creating", loaded: 0, total: 100 * MB }} />)
    expect(screen.getByText("Preparing upload...")).toBeInTheDocument()
    expect(screen.queryByText(/MB of/)).not.toBeInTheDocument()

    rerender(<UploadProgressModal progress={{ phase: "finalizing", loaded: 100 * MB, total: 100 * MB }} />)
    expect(screen.getByText("Upload complete, finishing up...")).toBeInTheDocument()
  })
})
