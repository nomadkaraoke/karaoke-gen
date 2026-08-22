"use client"

import { useRef, useEffect, useState, useCallback } from "react"
import { useTranslations } from "next-intl"
import { Loader2 } from "lucide-react"

interface KaraokeBackgroundPreviewProps {
  backgroundUrl?: string  // Object URL from File, or undefined for solid color
  backgroundColor?: string  // Solid color hex (used when no backgroundUrl)
  sungColor?: string  // Highlight color for sung lyrics
  unsungColor?: string  // Color for unsung lyrics
}

// 4K coordinate space (the sample lyrics are laid out in it); the canvas renders at
// the much smaller preview resolution below via a scale transform.
const CANVAS_W = 3840
const CANVAS_H = 2160

const PREVIEW_W = 960
const PREVIEW_H = 540
const MAX_DPR = 2

// Low-res background for the preview (the 4K asset stays for the real render path).
const KARAOKE_BG_SRC = "/karaoke-bg-preview.png"

/** Warm the karaoke background cache ahead of time (e.g. on Step 2). */
export function preloadKaraokeBg(): void {
  if (typeof window === "undefined") return
  const img = new Image()
  img.src = KARAOKE_BG_SRC
}

// 4 sample lines to match real karaoke video rendering
const SAMPLE_LINES = [
  "Don't say that you don't understand",
  "This is California Babylon my man",
  "Don't say that you don't understand",
  "Don't say that you can't comprehend",
]

// Default karaoke colors (matching Nomad theme)
const DEFAULT_SUNG_COLOR = "#7070F7"
const DEFAULT_UNSUNG_COLOR = "#ffffff"

export function KaraokeBackgroundPreview({
  backgroundUrl,
  backgroundColor,
  sungColor,
  unsungColor,
}: KaraokeBackgroundPreviewProps) {
  const t = useTranslations("karaokeBackgroundPreview")
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [ready, setReady] = useState(false)

  const effectiveSungColor = sungColor || DEFAULT_SUNG_COLOR
  const effectiveUnsungColor = unsungColor || DEFAULT_UNSUNG_COLOR

  const draw = useCallback(async () => {
    const canvas = canvasRef.current
    if (!canvas) return

    // Size the backing store to the preview resolution (dpr-aware, capped) and
    // scale the 4K coordinate space down to it.
    const dpr = Math.min((typeof window !== "undefined" && window.devicePixelRatio) || 1, MAX_DPR)
    const cw = Math.round(PREVIEW_W * dpr)
    const ch = Math.round(PREVIEW_H * dpr)
    if (canvas.width !== cw) canvas.width = cw
    if (canvas.height !== ch) canvas.height = ch

    const ctx = canvas.getContext("2d")
    if (!ctx) return
    ctx.setTransform(cw / CANVAS_W, 0, 0, ch / CANVAS_H, 0, 0)

    // Draw background: custom image > solid color > default Nomad theme image
    const bgSrc = backgroundUrl || (backgroundColor ? null : KARAOKE_BG_SRC)
    if (bgSrc) {
      try {
        const img = await new Promise<HTMLImageElement>((resolve, reject) => {
          const el = new Image()
          el.onload = () => resolve(el)
          el.onerror = reject
          el.src = bgSrc
        })
        ctx.drawImage(img, 0, 0, CANVAS_W, CANVAS_H)
      } catch {
        ctx.fillStyle = backgroundColor || "#000000"
        ctx.fillRect(0, 0, CANVAS_W, CANVAS_H)
      }
    } else {
      ctx.fillStyle = backgroundColor || "#000000"
      ctx.fillRect(0, 0, CANVAS_W, CANVAS_H)
    }

    // Draw 4 lines of sample lyrics, vertically centered
    const fontSize = 120
    const fontFamily = "'Arial', 'Helvetica', sans-serif"
    ctx.font = `700 ${fontSize}px ${fontFamily}`
    ctx.textAlign = "center"
    ctx.textBaseline = "middle"

    const centerX = CANVAS_W / 2
    const lineHeight = fontSize * 1.5
    const totalHeight = lineHeight * SAMPLE_LINES.length
    const startY = (CANVAS_H - totalHeight) / 2 + lineHeight / 2

    SAMPLE_LINES.forEach((line, i) => {
      const y = startY + i * lineHeight

      if (i === 1) {
        // Line 2: partially "sung" — highlight first portion
        const words = line.split(" ")
        const midPoint = 3 // "This is Califor" split mid-word effect
        const sungText = words.slice(0, midPoint).join(" ")
        // Simulate mid-word highlight: "Califor" is partially sung
        const sungPart = sungText.slice(0, -2)
        const transitionChar = sungText.slice(-2)
        const unsungPart = words.slice(midPoint).join(" ")

        const fullText = line
        const fullWidth = ctx.measureText(fullText).width
        const lineStartX = centerX - fullWidth / 2

        // Sung portion
        ctx.fillStyle = effectiveSungColor
        ctx.textAlign = "left"
        ctx.fillText(sungPart, lineStartX, y)

        // Transition characters (still sung color)
        const sungPartWidth = ctx.measureText(sungPart).width
        ctx.fillStyle = effectiveSungColor
        ctx.fillText(transitionChar, lineStartX + sungPartWidth, y)

        // Unsung portion
        const transWidth = ctx.measureText(transitionChar).width
        ctx.fillStyle = effectiveUnsungColor
        ctx.fillText(" " + unsungPart, lineStartX + sungPartWidth + transWidth, y)

        ctx.textAlign = "center"
      } else if (i === 0) {
        // Line 1: fully sung (already passed)
        ctx.fillStyle = effectiveSungColor
        ctx.fillText(line, centerX, y)
      } else {
        // Lines 3-4: unsung (upcoming)
        ctx.fillStyle = effectiveUnsungColor
        ctx.fillText(line, centerX, y)
      }
    })

    setReady(true)
  }, [backgroundUrl, backgroundColor, effectiveSungColor, effectiveUnsungColor])

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
          data-testid="karaoke-background-preview-loading"
        >
          <Loader2 className="w-5 h-5 animate-spin" />
          <span className="text-xs">{t('loading')}</span>
        </div>
      )}
    </div>
  )
}
