import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { OwnInstrumentalField, formatDuration } from "../OwnInstrumentalField"
import * as upload from "@/lib/upload"

jest.mock("@/lib/upload", () => {
  const actual = jest.requireActual("@/lib/upload")
  return { ...actual, getAudioFileDuration: jest.fn() }
})
const getDuration = upload.getAudioFileDuration as jest.Mock

const mix = new File(["m"], "song.wav", { type: "audio/wav" })
const inst = new File(["i"], "inst.wav", { type: "audio/wav" })

function pick(file: File) {
  fireEvent.change(screen.getByTestId("own-instrumental-input"), { target: { files: [file] } })
}

describe("OwnInstrumentalField", () => {
  beforeEach(() => getDuration.mockReset())

  it("accepts an instrumental that matches the song length", async () => {
    getDuration.mockResolvedValueOnce(200).mockResolvedValueOnce(200.3)
    const onChange = jest.fn()
    render(<OwnInstrumentalField mixFile={mix} file={null} onChange={onChange} />)

    pick(inst)
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(inst))
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  })

  it("rejects a mismatched instrumental with a readable error", async () => {
    getDuration.mockResolvedValueOnce(200).mockResolvedValueOnce(185)
    const onChange = jest.fn()
    render(<OwnInstrumentalField mixFile={mix} file={null} onChange={onChange} />)

    pick(inst)
    expect(await screen.findByRole("alert")).toHaveTextContent("This instrumental is 3:05 long but your song is 3:20")
    expect(onChange).toHaveBeenCalledWith(null)
    expect(onChange).not.toHaveBeenCalledWith(inst)
  })

  it("accepts the file when the browser can't read durations (backend re-checks)", async () => {
    getDuration.mockResolvedValue(null)
    const onChange = jest.fn()
    render(<OwnInstrumentalField mixFile={mix} file={null} onChange={onChange} />)

    pick(inst)
    await waitFor(() => expect(onChange).toHaveBeenCalledWith(inst))
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
