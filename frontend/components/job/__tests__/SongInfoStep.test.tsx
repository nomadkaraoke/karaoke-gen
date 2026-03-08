/**
 * Tests for SongInfoStep component.
 *
 * Key behavior tested:
 * - Submit button disabled when artist or title is empty
 * - Submit button enabled when both fields have content
 * - Form submission calls onNext
 * - Input changes call respective handlers
 * - Tip text is rendered (guidance for users)
 */

import { render, screen, fireEvent } from "@testing-library/react"
import { SongInfoStep } from "../steps/SongInfoStep"

const defaultProps = {
  artist: "",
  title: "",
  onArtistChange: jest.fn(),
  onTitleChange: jest.fn(),
  onNext: jest.fn(),
  disabled: false,
}

function renderStep(overrides?: Partial<typeof defaultProps>) {
  return render(<SongInfoStep {...defaultProps} {...overrides} />)
}

describe("SongInfoStep", () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it("renders artist and title input fields", () => {
    renderStep()
    expect(screen.getByTestId("guided-artist-input")).toBeInTheDocument()
    expect(screen.getByTestId("guided-title-input")).toBeInTheDocument()
  })

  it("submit button is disabled when artist is empty", () => {
    renderStep({ artist: "", title: "Some Song" })
    const button = screen.getByRole("button", { name: /choose audio/i })
    expect(button).toBeDisabled()
  })

  it("submit button is disabled when title is empty", () => {
    renderStep({ artist: "Some Artist", title: "" })
    const button = screen.getByRole("button", { name: /choose audio/i })
    expect(button).toBeDisabled()
  })

  it("submit button is disabled when both fields are whitespace-only", () => {
    renderStep({ artist: "   ", title: "   " })
    const button = screen.getByRole("button", { name: /choose audio/i })
    expect(button).toBeDisabled()
  })

  it("submit button is enabled when both fields have content", () => {
    renderStep({ artist: "Queen", title: "Bohemian Rhapsody" })
    const button = screen.getByRole("button", { name: /choose audio/i })
    expect(button).not.toBeDisabled()
  })

  it("clicking submit calls onNext when fields are filled", () => {
    const onNext = jest.fn()
    renderStep({ artist: "Queen", title: "Bohemian Rhapsody", onNext })
    fireEvent.click(screen.getByRole("button", { name: /choose audio/i }))
    expect(onNext).toHaveBeenCalledTimes(1)
  })

  it("form submission via Enter calls onNext", () => {
    const onNext = jest.fn()
    renderStep({ artist: "Queen", title: "Bohemian Rhapsody", onNext })
    fireEvent.submit(screen.getByTestId("guided-artist-input").closest("form")!)
    expect(onNext).toHaveBeenCalledTimes(1)
  })

  it("does not call onNext on submit when fields are empty", () => {
    const onNext = jest.fn()
    renderStep({ artist: "", title: "", onNext })
    fireEvent.submit(screen.getByTestId("guided-artist-input").closest("form")!)
    expect(onNext).not.toHaveBeenCalled()
  })

  it("typing in artist input calls onArtistChange", () => {
    const onArtistChange = jest.fn()
    renderStep({ onArtistChange })
    fireEvent.change(screen.getByTestId("guided-artist-input"), {
      target: { value: "ABBA" },
    })
    expect(onArtistChange).toHaveBeenCalledWith("ABBA")
  })

  it("typing in title input calls onTitleChange", () => {
    const onTitleChange = jest.fn()
    renderStep({ onTitleChange })
    fireEvent.change(screen.getByTestId("guided-title-input"), {
      target: { value: "Waterloo" },
    })
    expect(onTitleChange).toHaveBeenCalledWith("Waterloo")
  })

  it("disables inputs when disabled prop is true", () => {
    renderStep({ disabled: true })
    expect(screen.getByTestId("guided-artist-input")).toBeDisabled()
    expect(screen.getByTestId("guided-title-input")).toBeDisabled()
    expect(screen.getByRole("button", { name: /choose audio/i })).toBeDisabled()
  })

  it("shows tip about specific versions", () => {
    renderStep()
    expect(
      screen.getByText(/looking for a specific version/i)
    ).toBeInTheDocument()
  })

  it("shows guidance about custom lyrics", () => {
    renderStep()
    expect(
      screen.getByText(/making a video with custom lyrics/i)
    ).toBeInTheDocument()
  })

  it("shows guidance about unreleased songs", () => {
    renderStep()
    expect(
      screen.getByText(/have an unreleased song/i)
    ).toBeInTheDocument()
  })
})
