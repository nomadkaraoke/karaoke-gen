/**
 * Tests for VisibilityStep component.
 *
 * Key behavior tested:
 * - Action buttons at top that select visibility AND continue in one click
 * - "Publish & Share" calls onPrivateChange(false) + onNext()
 * - "Keep Private" calls onPrivateChange(true) + onNext()
 * - Detail cards toggle the radio selection (without advancing)
 * - Back button calls onBack
 */

import { render, screen, fireEvent } from "@testing-library/react"
import { VisibilityStep } from "../steps/VisibilityStep"

const defaultProps = {
  isPrivate: false,
  onPrivateChange: jest.fn(),
  onNext: jest.fn(),
  onBack: jest.fn(),
  disabled: false,
}

function renderStep(overrides?: Partial<typeof defaultProps>) {
  return render(<VisibilityStep {...defaultProps} {...overrides} />)
}

describe("VisibilityStep", () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it("renders the question heading", () => {
    renderStep()
    expect(screen.getByText("How should your video be shared?")).toBeInTheDocument()
  })

  it("renders Publish & Share and Keep Private action buttons", () => {
    renderStep()
    expect(screen.getByText("Publish & Share")).toBeInTheDocument()
    expect(screen.getByText("Keep Private")).toBeInTheDocument()
  })

  it("clicking 'Publish & Share' calls onPrivateChange(false) and onNext()", () => {
    const onPrivateChange = jest.fn()
    const onNext = jest.fn()
    renderStep({ onPrivateChange, onNext })

    fireEvent.click(screen.getByText("Publish & Share"))

    expect(onPrivateChange).toHaveBeenCalledWith(false)
    expect(onNext).toHaveBeenCalledTimes(1)
  })

  it("clicking 'Keep Private' calls onPrivateChange(true) and onNext()", () => {
    const onPrivateChange = jest.fn()
    const onNext = jest.fn()
    renderStep({ onPrivateChange, onNext })

    fireEvent.click(screen.getByText("Keep Private"))

    expect(onPrivateChange).toHaveBeenCalledWith(true)
    expect(onNext).toHaveBeenCalledTimes(1)
  })

  it("clicking Back button calls onBack", () => {
    const onBack = jest.fn()
    renderStep({ onBack })

    fireEvent.click(screen.getByText("Back"))

    expect(onBack).toHaveBeenCalledTimes(1)
  })

  it("renders Published detail card with Recommended badge", () => {
    renderStep()
    expect(screen.getByText("Published")).toBeInTheDocument()
    expect(screen.getByText("Recommended")).toBeInTheDocument()
  })

  it("renders Private detail card", () => {
    renderStep()
    expect(screen.getByText("Private")).toBeInTheDocument()
  })

  it("clicking Published detail card calls onPrivateChange(false) without calling onNext", () => {
    const onPrivateChange = jest.fn()
    const onNext = jest.fn()
    renderStep({ onPrivateChange, onNext })

    // Click the Published detail card (not the action button)
    fireEvent.click(screen.getByText("Published"))

    expect(onPrivateChange).toHaveBeenCalledWith(false)
    expect(onNext).not.toHaveBeenCalled()
  })

  it("clicking Private detail card calls onPrivateChange(true) without calling onNext", () => {
    const onPrivateChange = jest.fn()
    const onNext = jest.fn()
    renderStep({ onPrivateChange, onNext })

    // Click the Private detail card (not the action button)
    fireEvent.click(screen.getByText("Private"))

    expect(onPrivateChange).toHaveBeenCalledWith(true)
    expect(onNext).not.toHaveBeenCalled()
  })

  it("shows changeable note", () => {
    renderStep()
    expect(
      screen.getByText("You can change the visibility anytime after the video is produced.")
    ).toBeInTheDocument()
  })

  it("disables all buttons when disabled prop is true", () => {
    renderStep({ disabled: true })

    // Both action buttons should be disabled
    const publishButton = screen.getByText("Publish & Share").closest("button")
    const privateButton = screen.getByText("Keep Private").closest("button")
    const backButton = screen.getByText("Back").closest("button")

    expect(publishButton).toBeDisabled()
    expect(privateButton).toBeDisabled()
    expect(backButton).toBeDisabled()
  })
})
