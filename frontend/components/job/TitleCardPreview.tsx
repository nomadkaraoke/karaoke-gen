"use client"

import { useRef, useEffect, useState, useCallback } from "react"
import { useTranslations } from 'next-intl'
import { Loader2 } from "lucide-react"

interface TitleCardPreviewProps {
  artist: string
  title: string
  customBackgroundUrl?: string  // Object URL from File for custom background
  backgroundColor?: string     // Solid color hex (used when no customBackgroundUrl)
  titleColor?: string           // Hex color override (default #ffffff)
  artistColor?: string          // Hex color override (default #ffdf6b)
}

// Matches the Nomad theme title card (style_params.json from GCS):
// - 3840x2160 canvas (16:9)
// - Background: karaoke-title-screen-background-nomad-4k.png
// - Font: AvenirNext-Bold.ttf
// - Title: white (#ffffff), uppercase, region 370,980,3100,350
// - Artist: golden yellow (#ffdf6b), uppercase, region 370,1400,3100,450

// Coordinate space the title-card layout is authored in (style_params.json is in
// 4K). All region/font constants below are in this space; the canvas renders at a
// much smaller preview resolution (see PREVIEW_*) via a scale transform, so a small
// thumbnail no longer allocates a 4K (3840×2160) backing store + 4K PNG.
const CANVAS_W = 3840
const CANVAS_H = 2160

// Actual canvas backing-store resolution for the preview (multiplied by the
// device pixel ratio, capped, at draw time). Plenty sharp for the thumbnail.
const PREVIEW_W = 960
const PREVIEW_H = 540
const MAX_DPR = 2

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

// Low-res background sized for the preview (the full 4K asset stays for the real
// render path). Cached at module scope so it loads once per session.
const TITLE_CARD_BG_SRC = "/title-card-bg-preview.png"
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
    img.src = TITLE_CARD_BG_SRC
  })
}

/** Warm the title-card background cache ahead of time (e.g. on Step 2) so the
 *  Step 4 preview paints instantly. Safe to call repeatedly; failures are ignored. */
export function preloadTitleCardBg(): void {
  void loadBgImage().catch(() => {})
}

/** Test-only: clear the module-level bg cache so each test starts from a cold load. */
export function __resetTitleCardBgCacheForTest(): void {
  bgImageCache = null
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

export function TitleCardPreview({ artist, title, customBackgroundUrl, backgroundColor, titleColor, artistColor }: TitleCardPreviewProps) {
  const t = useTranslations('titleCardPreview')
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [ready, setReady] = useState(false)

  const effectiveTitleColor = titleColor || TITLE_COLOR
  const effectiveArtistColor = artistColor || ARTIST_COLOR

  const draw = useCallback(async () => {
    const canvas = canvasRef.current
    if (!canvas) return

    // Size the backing store to the preview resolution (dpr-aware, capped) and
    // scale the 4K coordinate space down to it, so we never allocate a 4K canvas.
    const dpr = Math.min((typeof window !== "undefined" && window.devicePixelRatio) || 1, MAX_DPR)
    const cw = Math.round(PREVIEW_W * dpr)
    const ch = Math.round(PREVIEW_H * dpr)
    if (canvas.width !== cw) canvas.width = cw
    if (canvas.height !== ch) canvas.height = ch

    const ctx = canvas.getContext("2d")
    if (!ctx) return
    ctx.setTransform(cw / CANVAS_W, 0, 0, ch / CANVAS_H, 0, 0)

    // Draw background: custom image > solid color > default image
    try {
      if (customBackgroundUrl) {
        const img = await new Promise<HTMLImageElement>((resolve, reject) => {
          const el = new Image()
          el.onload = () => resolve(el)
          el.onerror = reject
          el.src = customBackgroundUrl
        })
        ctx.drawImage(img, 0, 0, CANVAS_W, CANVAS_H)
      } else if (backgroundColor) {
        ctx.fillStyle = backgroundColor
        ctx.fillRect(0, 0, CANVAS_W, CANVAS_H)
      } else {
        const bgImg = await loadBgImage()
        ctx.drawImage(bgImg, 0, 0, CANVAS_W, CANVAS_H)
      }
    } catch {
      ctx.fillStyle = backgroundColor || "#000000"
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
    const titleText = (title || t('songTitle')).toUpperCase()
    const artistText = (artist || t('artist')).toUpperCase()

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

    setReady(true)
  }, [artist, title, customBackgroundUrl, backgroundColor, effectiveTitleColor, effectiveArtistColor, t])

  useEffect(() => {
    draw()
  }, [draw])

  return (
    <div className="relative w-full" style={{ aspectRatio: "16/9" }}>
      <canvas
        ref={canvasRef}
        width={PREVIEW_W}
        height={PREVIEW_H}
        className="w-full h-full rounded-lg"
        style={{
          border: "1px solid var(--card-border)",
        }}
      />
      {!ready && (
        <div
          className="absolute inset-0 flex flex-col items-center justify-center gap-2 rounded-lg"
          style={{ backgroundColor: "var(--secondary)", color: "var(--text-muted)" }}
          data-testid="title-card-preview-loading"
        >
          <Loader2 className="w-5 h-5 animate-spin" />
          <span className="text-xs">{t('loading')}</span>
        </div>
      )}
    </div>
  )
}
