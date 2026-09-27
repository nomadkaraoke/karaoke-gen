import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { OwnInstrumentalField, formatDuration } from "../OwnInstrumentalField"
import * as upload from "@/lib/upload"

jest.mock("@/lib/upload", () => {
  const actual = jest.requireActual("@/lib/upload")
  return { ...actual, checkInstrumentalFile: jest.fn() }
})
const check = upload.checkInstrumentalFile as jest.Mock

const mix = new File(["m"], "song.wav", { type: "audio/wav" })
const inst = new File(["i"], "inst.wav", { type: "audio/wav" })

function pick(file: File) {
  fireEvent.change(screen.getByTestId("own-instrumental-input"), { target: { files: [file] } })
}

describe("OwnInstrumentalField", () => {
  beforeEach(() => check.mockReset())

  it("accepts an instrumental that matches the song length", async () => {
    check.mockResolvedValue({ ok: true })
    const onChange = jest.fn()
    render(<OwnInstrumentalField mixFile={mix} file={null} onChange={onChange} />)

    pick(inst)
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(inst))
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  })

  it("rejects a mismatched instrumental with a readable error", async () => {
    check.mockResolvedValue({ ok: false, reason: "mismatch", fileSeconds: 185, expectedSeconds: 200 })
    const onChange = jest.fn()
    render(<OwnInstrumentalField mixFile={mix} file={null} onChange={onChange} />)

    pick(inst)
    expect(await screen.findByRole("alert")).toHaveTextContent("This instrumental is 3:05 long but your song is 3:20")
    expect(onChange).toHaveBeenCalledWith(null)
    expect(onChange).not.toHaveBeenCalledWith(inst)
  })

  it("rejects an oversized instrumental with the size limit", async () => {
    check.mockResolvedValue({ ok: false, reason: "tooLarge", sizeMb: 350, maxMb: 200 })
    const onChange = jest.fn()
    render(<OwnInstrumentalField mixFile={mix} file={null} onChange={onChange} />)

    pick(inst)
    expect(await screen.findByRole("alert")).toHaveTextContent("This file is 350 MB, which is over the 200 MB limit")
    expect(onChange).toHaveBeenCalledWith(null)
  })

  it("checks the pick against the mix file", async () => {
    check.mockResolvedValue({ ok: true })
    render(<OwnInstrumentalField mixFile={mix} file={null} onChange={jest.fn()} />)
    pick(inst)
    await waitFor(() => expect(check).toHaveBeenCalledWith(inst, mix))
  })

  it("shows the chosen file and lets the user remove it", () => {
    const onChange = jest.fn()
    render(<OwnInstrumentalField mixFile={mix} file={inst} onChange={onChange} />)

    expect(screen.getByText(/Using inst.wav/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: /Remove/ }))
    expect(onChange).toHaveBeenCalledWith(null)
  })

  it("formats durations as m:ss", () => {
    expect(formatDuration(65.4)).toBe("1:05")
    expect(formatDuration(600)).toBe("10:00")
  })
})
