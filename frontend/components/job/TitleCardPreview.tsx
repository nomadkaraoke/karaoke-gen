"use client"

import { useRef, useEffect, useCallback } from "react"

interface TitleCardPreviewProps {
  artist: string
  title: string
  customBackgroundUrl?: string  // Object URL from File for custom background
  titleColor?: string           // Hex color override (default #ffffff)
  artistColor?: string          // Hex color override (default #ffdf6b)
}

// Matches the Nomad theme title card (style_params.json from GCS):
// - 3840x2160 canvas (16:9)
// - Background: karaoke-title-screen-background-nomad-4k.png
// - Font: AvenirNext-Bold.ttf
// - Title: white (#ffffff), uppercase, region 370,980,3100,350
// - Artist: golden yellow (#ffdf6b), uppercase, region 370,1400,3100,450

const CANVAS_W = 3840
const CANVAS_H = 2160

// Title region: x=370, y=980, w=3100, h=350
const TITLE_X = 370
const TITLE_Y = 980
const TITLE_W = 3100
const TITLE_H = 350

// Artist region: x=370, y=1400, w=3100, h=450
const ARTIST_X = 370
const ARTIST_Y = 1400
const ARTIST_W = 3100
const ARTIST_H = 450

const TITLE_COLOR = "#ffffff"
const ARTIST_COLOR = "#ffdf6b"

let bgImageCache: HTMLImageElement | null = null

function loadBgImage(): Promise<HTMLImageElement> {
  if (bgImageCache?.complete) return Promise.resolve(bgImageCache)
  return new Promise((resolve, reject) => {
    const img = new Image()
    img.onload = () => {
      bgImageCache = img
      resolve(img)
    }
    img.onerror = reject
    img.src = "/title-card-bg.png"
  })
}

function fitText(
  ctx: CanvasRenderingContext2D,
  text: string,
  maxWidth: number,
  maxHeight: number,
  fontFamily: string,
): { fontSize: number; lines: string[] } {
  for (let size = 500; size >= 40; size -= 10) {
    ctx.font = `700 ${size}px ${fontFamily}`
    const metrics = ctx.measureText(text)

    if (metrics.width <= maxWidth) {
      return { fontSize: size, lines: [text] }
    }

    // Try 2-line split
    const words = text.split(" ")
    if (words.length >= 2) {
      let bestSplit = 1
      let bestDiff = Infinity
      for (let i = 1; i < words.length; i++) {
        const line1 = words.slice(0, i).join(" ")
        const line2 = words.slice(i).join(" ")
        const diff = Math.abs(ctx.measureText(line1).width - ctx.measureText(line2).width)
        if (diff < bestDiff) {
          bestDiff = diff
          bestSplit = i
        }
      }
      const line1 = words.slice(0, bestSplit).join(" ")
      const line2 = words.slice(bestSplit).join(" ")
      const w1 = ctx.measureText(line1).width
      const w2 = ctx.measureText(line2).width
      const lineHeight = size * 1.1

      if (Math.max(w1, w2) <= maxWidth && lineHeight * 2 <= maxHeight) {
        return { fontSize: size, lines: [line1, line2] }
      }
    }
  }
  return { fontSize: 40, lines: [text] }
}

function drawTextBlock(
  ctx: CanvasRenderingContext2D,
  text: string,
  regionX: number,
  regionY: number,
  regionW: number,
  regionH: number,
  color: string,
  fontFamily: string,
) {
  if (!text) return

  ctx.fillStyle = color
  ctx.textAlign = "center"
  ctx.textBaseline = "middle"

  const { fontSize, lines } = fitText(ctx, text, regionW, regionH, fontFamily)
  ctx.font = `700 ${fontSize}px ${fontFamily}`

  const lineHeight = fontSize * 1.1
  const totalHeight = lineHeight * lines.length
  const startY = regionY + (regionH - totalHeight) / 2 + lineHeight / 2

  for (let i = 0; i < lines.length; i++) {
    ctx.fillText(lines[i], regionX + regionW / 2, startY + i * lineHeight)
  }
}

export function TitleCardPreview({ artist, title, customBackgroundUrl, titleColor, artistColor }: TitleCardPreviewProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  const effectiveTitleColor = titleColor || TITLE_COLOR
  const effectiveArtistColor = artistColor || ARTIST_COLOR

  const draw = useCallback(async () => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext("2d")
    if (!ctx) return

    // Draw background image (custom or default)
    try {
      if (customBackgroundUrl) {
        const img = await new Promise<HTMLImageElement>((resolve, reject) => {
          const el = new Image()
          el.onload = () => resolve(el)
          el.onerror = reject
          el.src = customBackgroundUrl
        })
        ctx.drawImage(img, 0, 0, CANVAS_W, CANVAS_H)
      } else {
        const bgImg = await loadBgImage()
        ctx.drawImage(bgImg, 0, 0, CANVAS_W, CANVAS_H)
      }
    } catch {
      ctx.fillStyle = "#000000"
      ctx.fillRect(0, 0, CANVAS_W, CANVAS_H)
    }

    // Get computed font family from CSS variable
    const computedFont = getComputedStyle(canvas).getPropertyValue("--font-title-card").trim()
    const fontFamily = computedFont || "'AvenirNext-Bold', 'Avenir Next', sans-serif"

    // Ensure font is loaded before rendering — canvas doesn't participate in CSS
    // font swap, so we must explicitly wait for the font to be available.
    const fontSpec = `700 100px ${fontFamily}`
    if (!document.fonts.check(fontSpec)) {
      try {
        await document.fonts.load(fontSpec)
      } catch {
        // Font failed to load; will render with fallback
      }
    }

    // Apply uppercase transform (matching style_params.json title_text_transform/artist_text_transform)
    const titleText = (title || "Song Title").toUpperCase()
    const artistText = (artist || "Artist").toUpperCase()

    // Draw title
    drawTextBlock(
      ctx,
      titleText,
      TITLE_X, TITLE_Y, TITLE_W, TITLE_H,
      title ? effectiveTitleColor : "rgba(255,255,255,0.25)",
      fontFamily,
    )

    // Draw artist
    drawTextBlock(
      ctx,
      artistText,
      ARTIST_X, ARTIST_Y, ARTIST_W, ARTIST_H,
      artist ? effectiveArtistColor : "rgba(255,223,107,0.25)",
      fontFamily,
    )
  }, [artist, title, customBackgroundUrl, effectiveTitleColor, effectiveArtistColor])

  useEffect(() => {
    draw()
  }, [draw])

  return (
    <canvas
      ref={canvasRef}
      width={CANVAS_W}
      height={CANVAS_H}
      className="w-full rounded-lg"
      style={{
        aspectRatio: "16/9",
        border: "1px solid var(--card-border)",
      }}
    />
  )
}
