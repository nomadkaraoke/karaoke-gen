import { render, act } from "@testing-library/react"
import { TitleCardPreview } from "../TitleCardPreview"

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
})
