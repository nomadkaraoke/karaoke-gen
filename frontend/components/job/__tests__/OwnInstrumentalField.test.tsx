import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { OwnInstrumentalField, formatDuration } from "../OwnInstrumentalField"
import * as upload from "@/lib/upload"

jest.mock("@/lib/upload", () => {
  const actual = jest.requireActual("@/lib/upload")
  return { ...actual, checkInstrumentalFile: jest.fn() }
})
const check = upload.checkInstrumentalFile as jest.Mock

const inst = new File(["i"], "inst.wav", { type: "audio/wav" })

function pick(file: File) {
  fireEvent.change(screen.getByTestId("own-instrumental-input"), { target: { files: [file] } })
}

describe("OwnInstrumentalField", () => {
  beforeEach(() => check.mockReset())

  it("accepts a picked instrumental", async () => {
    check.mockResolvedValue({ ok: true })
    const onChange = jest.fn()
    render(<OwnInstrumentalField file={null} onChange={onChange} />)

    pick(inst)
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(inst))
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  })

  it("rejects an oversized instrumental with the size limit", async () => {
    check.mockResolvedValue({ ok: false, reason: "tooLarge", sizeMb: 350, maxMb: 200 })
    const onChange = jest.fn()
    render(<OwnInstrumentalField file={null} onChange={onChange} />)

    pick(inst)
    expect(await screen.findByRole("alert")).toHaveTextContent("This file is 350 MB, which is over the 200 MB limit")
    expect(onChange).toHaveBeenCalledWith(null)
  })

  it("checks only the size cap — a length mismatch is lined up by the backend", async () => {
    check.mockResolvedValue({ ok: true })
    render(<OwnInstrumentalField file={null} onChange={jest.fn()} />)
    pick(inst)
    await waitFor(() => expect(check).toHaveBeenCalledWith(inst, null))
  })

  it("shows the chosen file and lets the user remove it", () => {
    const onChange = jest.fn()
    render(<OwnInstrumentalField file={inst} onChange={onChange} />)

    expect(screen.getByText(/Using inst.wav/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: /Remove/ }))
    expect(onChange).toHaveBeenCalledWith(null)
  })

  it("formats durations as m:ss", () => {
    expect(formatDuration(65.4)).toBe("1:05")
    expect(formatDuration(600)).toBe("10:00")
  })
})
