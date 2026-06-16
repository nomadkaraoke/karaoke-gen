import { render, act, screen } from "@testing-library/react"
import { TitleCardPreview, preloadTitleCardBg, __resetTitleCardBgCacheForTest } from "../TitleCardPreview"

// Module-level state shared with mock closures via reference
const state = {
  fillTextCalls: [] as Array<{ text: string; font: string }>,
  currentFont: "",
  fontCheckReturn: true,
  fontLoadResolve: null as (() => void) | null,
}

// Mock Image globally before any module code runs
global.Image = class MockImage {
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  _src = ""
  complete = true
  get src() {
    return this._src
  }
  set src(v: string) {
    this._src = v
    // Fire synchronously to avoid timing issues in tests
    Promise.resolve().then(() => this.onload?.())
  }
} as unknown as typeof Image

function setupMocks() {
  state.fillTextCalls = []
  state.currentFont = ""
  state.fontCheckReturn = true
  state.fontLoadResolve = null

  HTMLCanvasElement.prototype.getContext = jest.fn(() => ({
    setTransform: jest.fn(),
    drawImage: jest.fn(),
    fillRect: jest.fn(),
    fillText: jest.fn((text: string) => {
      state.fillTextCalls.push({ text, font: state.currentFont })
    }),
    measureText: jest.fn(() => ({ width: 100 })),
    get font() {
      return state.currentFont
    },
    set font(v: string) {
      state.currentFont = v
    },
    fillStyle: "",
    textAlign: "",
    textBaseline: "",
  })) as unknown as typeof HTMLCanvasElement.prototype.getContext

  window.getComputedStyle = jest.fn(() => ({
    getPropertyValue: (prop: string) => {
      if (prop === "--font-title-card") return "'__AvenirNext_abc123'"
      return ""
    },
  })) as unknown as typeof window.getComputedStyle

  Object.defineProperty(document, "fonts", {
    value: {
      check: jest.fn(() => state.fontCheckReturn),
      load: jest.fn(
        () =>
          new Promise<void>((resolve) => {
            state.fontLoadResolve = resolve
          }),
      ),
      ready: Promise.resolve(),
    },
    writable: true,
    configurable: true,
  })
}

beforeEach(() => {
  setupMocks()
  __resetTitleCardBgCacheForTest()
})

describe("TitleCardPreview", () => {
  it("renders title and artist text in uppercase", async () => {
    await act(async () => {
      render(<TitleCardPreview title="My Song" artist="Cool Band" />)
      await new Promise((r) => setTimeout(r, 100))
    })

    const texts = state.fillTextCalls.map((c) => c.text)
    expect(texts).toContain("MY SONG")
    expect(texts).toContain("COOL BAND")
  })

  it("uses font from CSS variable for canvas text", async () => {
    await act(async () => {
      render(<TitleCardPreview title="Test" artist="Artist" />)
      await new Promise((r) => setTimeout(r, 100))
    })

    const fonts = state.fillTextCalls.map((c) => c.font)
    expect(fonts.length).toBeGreaterThan(0)
    expect(fonts.every((f) => f.includes("'__AvenirNext_abc123'"))).toBe(true)
  })

  it("skips document.fonts.load when font is already available", async () => {
    state.fontCheckReturn = true

    await act(async () => {
      render(<TitleCardPreview title="Song" artist="Artist" />)
      await new Promise((r) => setTimeout(r, 100))
    })

    expect(document.fonts.check).toHaveBeenCalledWith(
      expect.stringContaining("'__AvenirNext_abc123'"),
    )
    expect(document.fonts.load).not.toHaveBeenCalled()
  })

  it("calls document.fonts.load when font is not yet available", async () => {
    state.fontCheckReturn = false

    await act(async () => {
      render(<TitleCardPreview title="Song" artist="Artist" />)
      await new Promise((r) => setTimeout(r, 100))
      // Resolve the pending font load promise
      state.fontLoadResolve?.()
      await new Promise((r) => setTimeout(r, 100))
    })

    expect(document.fonts.check).toHaveBeenCalledWith(
      expect.stringContaining("'__AvenirNext_abc123'"),
    )
    expect(document.fonts.load).toHaveBeenCalledWith(
      expect.stringContaining("'__AvenirNext_abc123'"),
    )
  })

  it("still renders text if font load fails", async () => {
    state.fontCheckReturn = false
    ;(document.fonts as { load: jest.Mock }).load = jest.fn().mockRejectedValue(new Error("fail"))

    await act(async () => {
      render(<TitleCardPreview title="Fallback" artist="Test" />)
      await new Promise((r) => setTimeout(r, 100))
    })

    const texts = state.fillTextCalls.map((c) => c.text)
    expect(texts).toContain("FALLBACK")
  })

  it("uses custom colors when provided", async () => {
    state.fillStyleCalls = []

    // Override the mock to track fillStyle assignments
    const fillStyles: string[] = []
    HTMLCanvasElement.prototype.getContext = jest.fn(() => {
      let _fillStyle = ""
      let _font = ""
      return {
        setTransform: jest.fn(),
        drawImage: jest.fn(),
        fillRect: jest.fn(),
        fillText: jest.fn((text: string) => {
          state.fillTextCalls.push({ text, font: _font })
          fillStyles.push(_fillStyle)
        }),
        measureText: jest.fn(() => ({ width: 100 })),
        get font() { return _font },
        set font(v: string) { _font = v; state.currentFont = v },
        get fillStyle() { return _fillStyle },
        set fillStyle(v: string) { _fillStyle = v },
        textAlign: "",
        textBaseline: "",
      }
    }) as unknown as typeof HTMLCanvasElement.prototype.getContext

    await act(async () => {
      render(
        <TitleCardPreview
          title="Custom"
          artist="Colors"
          titleColor="#ff0000"
          artistColor="#00ff00"
        />
      )
      await new Promise((r) => setTimeout(r, 100))
    })

    // The fillStyle should include our custom colors
    expect(fillStyles).toContain("#ff0000")
    expect(fillStyles).toContain("#00ff00")
  })

  it("renders the canvas at preview resolution, not 4K", async () => {
    let result: ReturnType<typeof render>
    await act(async () => {
      result = render(<TitleCardPreview title="Test" artist="Artist" />)
      await new Promise((r) => setTimeout(r, 100))
    })

    const canvasEl = result!.container.querySelector("canvas")
    expect(canvasEl).not.toBeNull()
    // dpr defaults to 1 in jsdom → 960×540 backing store (was 3840×2160).
    expect(canvasEl!.width).toBe(960)
    expect(canvasEl!.height).toBe(540)
  })

  it("loads the low-res preview background by default", async () => {
    const loadedSrcs: string[] = []
    global.Image = class MockImageDefault {
      onload: (() => void) | null = null
      onerror: (() => void) | null = null
      _src = ""
      complete = true
      get src() { return this._src }
      set src(v: string) {
        this._src = v
        loadedSrcs.push(v)
        Promise.resolve().then(() => this.onload?.())
      }
    } as unknown as typeof Image

    await act(async () => {
      render(<TitleCardPreview title="Test" artist="Artist" />)
      await new Promise((r) => setTimeout(r, 100))
    })

    expect(loadedSrcs).toContain("/title-card-bg-preview.png")
    expect(loadedSrcs).not.toContain("/title-card-bg.png")
  })

  it("shows a loading spinner before the first draw completes", () => {
    // Hold the font load open so draw() never reaches setReady(true).
    state.fontCheckReturn = false
    render(<TitleCardPreview title="Song" artist="Artist" />)
    expect(screen.getByTestId("title-card-preview-loading")).toBeInTheDocument()
  })

  it("preloadTitleCardBg loads the low-res asset without throwing", () => {
    const loadedSrcs: string[] = []
    global.Image = class MockImagePreload {
      onload: (() => void) | null = null
      onerror: (() => void) | null = null
      _src = ""
      complete = false
      get src() { return this._src }
      set src(v: string) { this._src = v; loadedSrcs.push(v) }
    } as unknown as typeof Image

    expect(() => preloadTitleCardBg()).not.toThrow()
    expect(loadedSrcs).toContain("/title-card-bg-preview.png")
  })

  it("loads custom background image when customBackgroundUrl is provided", async () => {
    const loadedSrcs: string[] = []
    global.Image = class MockImageCustom {
      onload: (() => void) | null = null
      onerror: (() => void) | null = null
      _src = ""
      complete = true
      get src() { return this._src }
      set src(v: string) {
        this._src = v
        loadedSrcs.push(v)
        Promise.resolve().then(() => this.onload?.())
      }
    } as unknown as typeof Image

    await act(async () => {
      render(
        <TitleCardPreview
          title="Test"
          artist="Artist"
          customBackgroundUrl="blob:http://localhost/custom-bg"
        />
      )
      await new Promise((r) => setTimeout(r, 100))
    })

    // Should load the custom background URL instead of the default
    expect(loadedSrcs).toContain("blob:http://localhost/custom-bg")
  })
})
