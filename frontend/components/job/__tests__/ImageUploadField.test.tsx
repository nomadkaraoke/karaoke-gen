import { render, screen, fireEvent } from "@testing-library/react"
import { ImageUploadField } from "../ImageUploadField"

describe("ImageUploadField", () => {
  it("renders label and drop zone when no file selected", () => {
    render(
      <ImageUploadField
        label="Karaoke Background"
        file={null}
        onChange={jest.fn()}
      />
    )

    expect(screen.getByText("Karaoke Background")).toBeInTheDocument()
    expect(screen.getByText(/Drop an image here/)).toBeInTheDocument()
    expect(screen.getByText("PNG or JPG, max 10 MB")).toBeInTheDocument()
  })

  it("renders description when provided", () => {
    render(
      <ImageUploadField
        label="Background"
        description="Upload a 16:9 image"
        file={null}
        onChange={jest.fn()}
      />
    )

    expect(screen.getByText("Upload a 16:9 image")).toBeInTheDocument()
  })

  it("shows file info and remove button when file is selected", () => {
    const file = new File(["data"], "test-bg.png", { type: "image/png" })
    Object.defineProperty(file, "size", { value: 1024 * 500 }) // 500 KB

    // Mock URL.createObjectURL
    const mockUrl = "blob:http://localhost/mock-url"
    global.URL.createObjectURL = jest.fn(() => mockUrl)
    global.URL.revokeObjectURL = jest.fn()

    render(
      <ImageUploadField
        label="Background"
        file={file}
        onChange={jest.fn()}
      />
    )

    expect(screen.getByText("test-bg.png")).toBeInTheDocument()
    // Drop zone should not be visible
    expect(screen.queryByText(/Drop an image here/)).not.toBeInTheDocument()
  })

  it("calls onChange with null when remove button is clicked", () => {
    const file = new File(["data"], "bg.png", { type: "image/png" })
    const onChange = jest.fn()

    global.URL.createObjectURL = jest.fn(() => "blob:mock")
    global.URL.revokeObjectURL = jest.fn()

    render(
      <ImageUploadField
        label="Background"
        file={file}
        onChange={onChange}
      />
    )

    // Find and click the remove button (X icon)
    const removeBtn = screen.getByRole("button")
    fireEvent.click(removeBtn)

    expect(onChange).toHaveBeenCalledWith(null)
  })

  it("accepts valid PNG file via input change", () => {
    const onChange = jest.fn()

    render(
      <ImageUploadField
        label="Background"
        file={null}
        onChange={onChange}
      />
    )

    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    expect(input).toBeTruthy()

    const file = new File(["data"], "bg.png", { type: "image/png" })
    Object.defineProperty(file, "size", { value: 1024 * 100 }) // 100 KB

    fireEvent.change(input, { target: { files: [file] } })

    expect(onChange).toHaveBeenCalledWith(file)
  })

  it("accepts valid JPEG file", () => {
    const onChange = jest.fn()

    render(
      <ImageUploadField
        label="Background"
        file={null}
        onChange={onChange}
      />
    )

    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File(["data"], "bg.jpg", { type: "image/jpeg" })
    Object.defineProperty(file, "size", { value: 1024 * 100 })

    fireEvent.change(input, { target: { files: [file] } })

    expect(onChange).toHaveBeenCalledWith(file)
  })

  it("rejects non-image file types", () => {
    const onChange = jest.fn()

    render(
      <ImageUploadField
        label="Background"
        file={null}
        onChange={onChange}
      />
    )

    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File(["data"], "doc.pdf", { type: "application/pdf" })

    fireEvent.change(input, { target: { files: [file] } })

    expect(onChange).not.toHaveBeenCalled()
    expect(screen.getByText("Only PNG and JPG images are allowed.")).toBeInTheDocument()
  })

  it("rejects files over 10MB", () => {
    const onChange = jest.fn()

    render(
      <ImageUploadField
        label="Background"
        file={null}
        onChange={onChange}
      />
    )

    const input = document.querySelector('input[type="file"]') as HTMLInputElement
    const file = new File(["data"], "huge.png", { type: "image/png" })
    Object.defineProperty(file, "size", { value: 11 * 1024 * 1024 }) // 11 MB

    fireEvent.change(input, { target: { files: [file] } })

    expect(onChange).not.toHaveBeenCalled()
    expect(screen.getByText(/File too large/)).toBeInTheDocument()
  })

  it("disables interaction when disabled prop is true", () => {
    render(
      <ImageUploadField
        label="Background"
        file={null}
        onChange={jest.fn()}
        disabled
      />
    )

    // The drop zone should have opacity-50 class
    const dropZone = screen.getByText(/Drop an image here/).closest("div")
    expect(dropZone?.className).toContain("opacity-50")
    expect(dropZone?.className).toContain("cursor-not-allowed")
  })
})
