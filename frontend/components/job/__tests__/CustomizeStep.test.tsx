import { render, screen, fireEvent } from "@testing-library/react"
import { CustomizeStep } from "../steps/CustomizeStep"

// Mock canvas-based components — they require browser APIs not available in jsdom
jest.mock("../TitleCardPreview", () => ({
  TitleCardPreview: ({ artist, title, customBackgroundUrl, titleColor, artistColor }: any) => (
    <div data-testid="title-card-preview"
      data-artist={artist}
      data-title={title}
      data-custom-bg={customBackgroundUrl || ""}
      data-title-color={titleColor || ""}
      data-artist-color={artistColor || ""}
    />
  ),
}))

jest.mock("../KaraokeBackgroundPreview", () => ({
  KaraokeBackgroundPreview: ({ backgroundUrl }: any) => (
    <div data-testid="karaoke-bg-preview" data-url={backgroundUrl} />
  ),
}))

jest.mock("../ImageUploadField", () => ({
  ImageUploadField: ({ label, file, onChange, disabled }: any) => (
    <div data-testid={`upload-${label.replace(/\s+/g, "-").toLowerCase()}`}>
      <span>{label}</span>
      {file && <span data-testid="file-name">{file.name}</span>}
      <button data-testid={`upload-btn-${label.replace(/\s+/g, "-").toLowerCase()}`}
        onClick={() => onChange(new File(["data"], "test.png", { type: "image/png" }))}
        disabled={disabled}
      >
        Upload
      </button>
      <button data-testid={`clear-btn-${label.replace(/\s+/g, "-").toLowerCase()}`}
        onClick={() => onChange(null)}
      >
        Clear
      </button>
    </div>
  ),
}))

const defaultProps = {
  artist: "Test Artist",
  title: "Test Song",
  displayArtist: "",
  displayTitle: "",
  onDisplayArtistChange: jest.fn(),
  onDisplayTitleChange: jest.fn(),
  isPrivate: false,
  onPrivateChange: jest.fn(),
  karaokeBackground: null as File | null,
  onKaraokeBackgroundChange: jest.fn(),
  introBackground: null as File | null,
  onIntroBackgroundChange: jest.fn(),
  colorOverrides: {},
  onColorOverridesChange: jest.fn(),
  onConfirm: jest.fn(),
  onBack: jest.fn(),
  isSubmitting: false,
}

describe("CustomizeStep", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    // Mock URL.createObjectURL for the component's useMemo
    global.URL.createObjectURL = jest.fn(() => "blob:mock")
    global.URL.revokeObjectURL = jest.fn()
  })

  it("renders title card preview with artist and title", () => {
    render(<CustomizeStep {...defaultProps} />)

    const preview = screen.getByTestId("title-card-preview")
    expect(preview).toBeInTheDocument()
    expect(preview.getAttribute("data-artist")).toBe("Test Artist")
    expect(preview.getAttribute("data-title")).toBe("Test Song")
  })

  it("uses displayArtist/displayTitle in preview when provided", () => {
    render(
      <CustomizeStep
        {...defaultProps}
        displayArtist="Custom Artist"
        displayTitle="Custom Title"
      />
    )

    const preview = screen.getByTestId("title-card-preview")
    expect(preview.getAttribute("data-artist")).toBe("Custom Artist")
    expect(preview.getAttribute("data-title")).toBe("Custom Title")
  })

  it("does NOT show custom video style section when isPrivate is false", () => {
    render(<CustomizeStep {...defaultProps} isPrivate={false} />)

    expect(screen.queryByText("Custom Video Style")).not.toBeInTheDocument()
    expect(screen.queryByText("Karaoke Background")).not.toBeInTheDocument()
    expect(screen.queryByText("Title Card Background")).not.toBeInTheDocument()
  })

  it("shows custom video style section when isPrivate is true", () => {
    render(<CustomizeStep {...defaultProps} isPrivate={true} />)

    expect(screen.getByText("Custom Video Style")).toBeInTheDocument()
    expect(screen.getByText("Karaoke Background")).toBeInTheDocument()
    expect(screen.getByText("Title Card Background")).toBeInTheDocument()
  })

  it("shows color pickers when isPrivate is true", () => {
    render(<CustomizeStep {...defaultProps} isPrivate={true} />)

    expect(screen.getByText("Artist Color")).toBeInTheDocument()
    expect(screen.getByText("Title Color")).toBeInTheDocument()
  })

  it("passes color overrides to title card preview", () => {
    render(
      <CustomizeStep
        {...defaultProps}
        isPrivate={true}
        colorOverrides={{ artist_color: "#ff0000", title_color: "#00ff00" }}
      />
    )

    const preview = screen.getByTestId("title-card-preview")
    expect(preview.getAttribute("data-artist-color")).toBe("#ff0000")
    expect(preview.getAttribute("data-title-color")).toBe("#00ff00")
  })

  it("toggles private checkbox and triggers onPrivateChange", () => {
    render(<CustomizeStep {...defaultProps} isPrivate={false} />)

    const checkbox = screen.getByRole("checkbox")
    fireEvent.click(checkbox)

    expect(defaultProps.onPrivateChange).toHaveBeenCalledWith(true)
  })

  it("calls onConfirm when create button is clicked", () => {
    render(<CustomizeStep {...defaultProps} />)

    const button = screen.getByText("Create Karaoke Video")
    fireEvent.click(button)

    expect(defaultProps.onConfirm).toHaveBeenCalledTimes(1)
  })

  it("shows loading state when isSubmitting", () => {
    render(<CustomizeStep {...defaultProps} isSubmitting={true} />)

    expect(screen.getByText("Creating...")).toBeInTheDocument()
  })

  it("disables form controls when isSubmitting", () => {
    render(<CustomizeStep {...defaultProps} isSubmitting={true} />)

    const createBtn = screen.getByText("Creating...").closest("button")
    expect(createBtn).toBeDisabled()
  })

  it("calls onBack when back button is clicked", () => {
    render(<CustomizeStep {...defaultProps} />)

    const backBtn = screen.getByText("Back")
    fireEvent.click(backBtn)

    expect(defaultProps.onBack).toHaveBeenCalledTimes(1)
  })

  it("shows karaoke background preview when file is provided and isPrivate", () => {
    const file = new File(["data"], "karaoke-bg.png", { type: "image/png" })

    render(
      <CustomizeStep
        {...defaultProps}
        isPrivate={true}
        karaokeBackground={file}
      />
    )

    expect(screen.getByTestId("karaoke-bg-preview")).toBeInTheDocument()
  })

  it("does NOT show karaoke background preview when no file", () => {
    render(
      <CustomizeStep
        {...defaultProps}
        isPrivate={true}
        karaokeBackground={null}
      />
    )

    expect(screen.queryByTestId("karaoke-bg-preview")).not.toBeInTheDocument()
  })

  it("passes custom intro background to title card preview", () => {
    const file = new File(["data"], "intro-bg.png", { type: "image/png" })

    render(
      <CustomizeStep
        {...defaultProps}
        isPrivate={true}
        introBackground={file}
      />
    )

    const preview = screen.getByTestId("title-card-preview")
    // Should have a blob URL for the custom background
    expect(preview.getAttribute("data-custom-bg")).toBe("blob:mock")
  })
})
