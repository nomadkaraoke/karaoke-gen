import { copyTitleCardLayoutToEnd, formatRegion, getField, hexToRgba, parseRegion, rgbaToHex, setField, stableStringify } from "../theme-utils"

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

describe("copyTitleCardLayoutToEnd", () => {
  const FALLBACK = { x: 370, y: 400, w: 3100, h: 400 }
  // Randy Vild's theme: small title card text, stock (large) end screen.
  const randy = {
    intro: {
      title_color: "#ffffff", artist_color: "#5093ce", title_text_transform: "uppercase", artist_text_transform: null,
      title_region: "370,620,3100,200", artist_region: "370,880,3100,200",
    },
    end: {
      title_color: "#ffffff", artist_color: "#ffdf6b", title_text_transform: "uppercase", artist_text_transform: "uppercase",
      title_region: "370,900,3100,350", artist_region: "370,1450,3100,200",
      extra_text: "THANK YOU FOR SINGING!", extra_text_region: "370,400,3100,400", extra_text_color: "#ff7acc",
    },
  }

  it("matches the title card and puts the closing message just above the title at the same size", () => {
    const end = copyTitleCardLayoutToEnd(randy, FALLBACK).end
    expect(end).toMatchObject({
      title_region: "370,620,3100,200", artist_region: "370,880,3100,200",
      artist_color: "#5093ce", artist_text_transform: null, title_text_transform: "uppercase",
      extra_text_region: "370,360,3100,200", extra_text: "THANK YOU FOR SINGING!", extra_text_color: "#ff7acc",
    })
  })

  it("puts the closing message below the artist when there's no room above the title", () => {
    const top = { ...randy, intro: { ...randy.intro, title_region: "370,100,3100,200", artist_region: "370,350,3100,200" } }
    expect(copyTitleCardLayoutToEnd(top, FALLBACK).end.extra_text_region).toBe("370,610,3100,200")
  })

  it("leaves the closing message alone when there is none or the title is hidden", () => {
    const noMessage = { ...randy, end: { ...randy.end, extra_text: null } }
    expect(copyTitleCardLayoutToEnd(noMessage, FALLBACK).end.extra_text_region).toBe("370,400,3100,400")
    const noTitle = { ...randy, intro: { ...randy.intro, title_region: undefined } }
    const end = copyTitleCardLayoutToEnd(noTitle, FALLBACK).end
    expect(end.title_region).toBeNull()
    expect(end.extra_text_region).toBe("370,400,3100,400")
  })
})
