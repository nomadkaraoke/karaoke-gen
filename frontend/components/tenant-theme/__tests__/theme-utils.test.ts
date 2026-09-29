import { formatRegion, getField, hexToRgba, parseRegion, rgbaToHex, setField, stableStringify } from "../theme-utils"

describe("theme-utils", () => {
  it("converts between hex and karaoke 'r, g, b, a' keeping alpha", () => {
    expect(rgbaToHex("61, 149, 197, 255")).toBe("#3d95c5")
    expect(rgbaToHex("junk", "#123456")).toBe("#123456")
    expect(hexToRgba("#3d95c5")).toBe("61, 149, 197, 255")
    expect(hexToRgba("#000000", "1, 2, 3, 128")).toBe("0, 0, 0, 128")
    expect(hexToRgba("not-a-colour", "1, 2, 3, 4")).toBe("1, 2, 3, 4")
  })

  it("parses regions and clamps them inside the 4K frame", () => {
    const fallback = { x: 1, y: 2, w: 3, h: 4 }
    expect(parseRegion("370, 980, 3100, 350", fallback)).toEqual({ x: 370, y: 980, w: 3100, h: 350 })
    expect(parseRegion("bad", fallback)).toBe(fallback)
    expect(formatRegion({ x: 370, y: 2100, w: 3100, h: 350 })).toBe("370,1810,3100,350")
    expect(formatRegion({ x: 900, y: 0, w: 3100, h: 5 })).toBe("740,0,3100,20")
  })

  it("sets fields immutably and compares drafts stably", () => {
    const a = { intro: { title_color: "#fff" }, karaoke: {} }
    const b = setField(a, "intro", "title_color", "#000")
    expect(a.intro.title_color).toBe("#fff")
    expect(getField(b, "intro", "title_color")).toBe("#000")
    expect(stableStringify({ b: 1, a: [1, { d: 2, c: 3 }] })).toBe(stableStringify({ a: [1, { c: 3, d: 2 }], b: 1 }))
  })
})
