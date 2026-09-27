import { render, screen, fireEvent } from "@testing-library/react"
import { CustomizeStep } from "../steps/CustomizeStep"

// Mock canvas-based components — they require browser APIs not available in jsdom
jest.mock("../TitleCardPreview", () => ({
  TitleCardPreview: ({ artist, title, customBackgroundUrl, titleColor, artistColor, backgroundColor }: any) => (
    <div data-testid="title-card-preview"
      data-artist={artist}
      data-title={title}
      data-custom-bg={customBackgroundUrl || ""}
      data-title-color={titleColor || ""}
      data-artist-color={artistColor || ""}
      data-bg-color={backgroundColor || ""}
    />
  ),
}))

jest.mock("../KaraokeBackgroundPreview", () => ({
  KaraokeBackgroundPreview: ({ backgroundUrl, backgroundColor, sungColor, unsungColor }: any) => (
    <div data-testid="karaoke-bg-preview"
      data-url={backgroundUrl || ""}
      data-bg-color={backgroundColor || ""}
      data-sung-color={sungColor || ""}
      data-unsung-color={unsungColor || ""}
    />
  ),
}))

jest.mock("../ImageUploadField", () => ({
  ImageUploadField: ({ label, description, file, onChange, disabled, hidePreview }: any) => (
    <div data-testid={`upload-${(label || description || "field").replace(/\s+/g, "-").toLowerCase().slice(0, 30)}`}>
      {label && <span>{label}</span>}
      {description && <span>{description}</span>}
      {file && <span data-testid="file-name">{file.name}</span>}
      <button
        data-testid={`upload-btn`}
        onClick={() => onChange(new File(["data"], "test.png", { type: "image/png" }))}
        disabled={disabled}
      >
        Upload
      </button>
      <button data-testid={`clear-btn`} onClick={() => onChange(null)}>
        Clear
      </button>
      {hidePreview && <span data-testid="hide-preview" />}
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
    global.URL.createObjectURL = jest.fn(() => "blob:mock")
    global.URL.revokeObjectURL = jest.fn()
  })

  describe("public mode (isPrivate=false)", () => {
    it("shows title card preview and display fields, but no style options", () => {
      render(<CustomizeStep {...defaultProps} isPrivate={false} />)

      // Display override fields are shown
      expect(screen.getByLabelText(/Title Card Artist/)).toBeInTheDocument()
      expect(screen.getByLabelText(/Title Card Title/)).toBeInTheDocument()

      // Title card preview is shown
      expect(screen.getByTestId("title-card-preview")).toBeInTheDocument()

      // No style customization or karaoke preview
      expect(screen.queryByText("Custom Video Style")).not.toBeInTheDocument()
      expect(screen.queryByTestId("karaoke-bg-preview")).not.toBeInTheDocument()
    })

    it("calls onConfirm when create button is clicked", () => {
      render(<CustomizeStep {...defaultProps} isPrivate={false} />)

      fireEvent.click(screen.getByText("Create Karaoke Video"))
      expect(defaultProps.onConfirm).toHaveBeenCalledTimes(1)
    })

    it("shows loading state when isSubmitting", () => {
      render(<CustomizeStep {...defaultProps} isPrivate={false} isSubmitting={true} />)
      expect(screen.getByText("Creating...")).toBeInTheDocument()
    })
  })

  describe("private mode (isPrivate=true)", () => {
    it("shows translated loading label when isSubmitting", () => {
      render(<CustomizeStep {...defaultProps} isPrivate={true} isSubmitting={true} />)
      expect(screen.getByText("Creating...")).toBeInTheDocument()
    })

    it("shows custom video style section with side-by-side previews", () => {
      render(<CustomizeStep {...defaultProps} isPrivate={true} />)

      expect(screen.getByText("Custom Video Style")).toBeInTheDocument()
      expect(screen.getByTestId("title-card-preview")).toBeInTheDocument()
      expect(screen.getByTestId("karaoke-bg-preview")).toBeInTheDocument()
    })

    it("renders title card preview with artist and title", () => {
      render(<CustomizeStep {...defaultProps} isPrivate={true} />)

      const preview = screen.getByTestId("title-card-preview")
      expect(preview.getAttribute("data-artist")).toBe("Test Artist")
      expect(preview.getAttribute("data-title")).toBe("Test Song")
    })

    it("uses displayArtist/displayTitle in preview when provided", () => {
      render(
        <CustomizeStep
          {...defaultProps}
          isPrivate={true}
          displayArtist="Custom Artist"
          displayTitle="Custom Title"
        />
      )

      const preview = screen.getByTestId("title-card-preview")
      expect(preview.getAttribute("data-artist")).toBe("Custom Artist")
      expect(preview.getAttribute("data-title")).toBe("Custom Title")
    })

    it("shows color pickers for title card and lyrics", () => {
      render(<CustomizeStep {...defaultProps} isPrivate={true} />)

      expect(screen.getByText("Artist Color")).toBeInTheDocument()
      expect(screen.getByText("Title Color")).toBeInTheDocument()
      expect(screen.getByText("Highlight Color")).toBeInTheDocument()
      expect(screen.getByText("Lyrics Color")).toBeInTheDocument()
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

    it("passes lyrics color overrides to karaoke preview", () => {
      render(
        <CustomizeStep
          {...defaultProps}
          isPrivate={true}
          colorOverrides={{ sung_lyrics_color: "#ff0000", unsung_lyrics_color: "#00ff00" }}
        />
      )

      const preview = screen.getByTestId("karaoke-bg-preview")
      expect(preview.getAttribute("data-sung-color")).toBe("#ff0000")
      expect(preview.getAttribute("data-unsung-color")).toBe("#00ff00")
    })

    it("shows background mode toggles", () => {
      render(<CustomizeStep {...defaultProps} isPrivate={true} />)

      // Two sets of Default/Image/Color toggles (one per canvas)
      const defaultBtns = screen.getAllByText("Default")
      const imageBtns = screen.getAllByText("Image")
      const colorBtns = screen.getAllByText("Color")

      expect(defaultBtns).toHaveLength(2)
      expect(imageBtns).toHaveLength(2)
      expect(colorBtns).toHaveLength(2)
    })

    it("calls onConfirm when create button is clicked", () => {
      render(<CustomizeStep {...defaultProps} isPrivate={true} />)

      fireEvent.click(screen.getByText("Create Karaoke Video"))
      expect(defaultProps.onConfirm).toHaveBeenCalledTimes(1)
    })

    it("shows loading state when isSubmitting", () => {
      render(<CustomizeStep {...defaultProps} isPrivate={true} isSubmitting={true} />)
      expect(screen.getByText("Creating...")).toBeInTheDocument()
    })
  })

  it("calls onBack when back button is clicked", () => {
    render(<CustomizeStep {...defaultProps} />)

    fireEvent.click(screen.getByText("Back"))
    expect(defaultProps.onBack).toHaveBeenCalledTimes(1)
  })

  it("disables form controls when isSubmitting", () => {
    render(<CustomizeStep {...defaultProps} isSubmitting={true} />)

    const createBtn = screen.getByText("Creating...").closest("button")
    expect(createBtn).toBeDisabled()
  })
})
