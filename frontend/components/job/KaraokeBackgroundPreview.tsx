"use client"

import { useRef, useEffect, useCallback } from "react"

interface KaraokeBackgroundPreviewProps {
  backgroundUrl: string  // Object URL from File
}

const CANVAS_W = 3840
const CANVAS_H = 2160

// Sample lyrics to show how text looks over the background
const SAMPLE_LINE_1 = "This is how your lyrics will look"
const SAMPLE_LINE_2 = "over the background image"

// Default karaoke colors (matching Nomad theme)
const SUNG_COLOR = "#7070F7"
const UNSUNG_COLOR = "#ffffff"

export function KaraokeBackgroundPreview({ backgroundUrl }: KaraokeBackgroundPreviewProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  const draw = useCallback(async () => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext("2d")
    if (!ctx) return

    // Draw background
    try {
      const img = await new Promise<HTMLImageElement>((resolve, reject) => {
        const el = new Image()
        el.onload = () => resolve(el)
        el.onerror = reject
        el.src = backgroundUrl
      })
      ctx.drawImage(img, 0, 0, CANVAS_W, CANVAS_H)
    } catch {
      ctx.fillStyle = "#000000"
      ctx.fillRect(0, 0, CANVAS_W, CANVAS_H)
    }

    // Draw sample lyrics in the lower third (where they typically appear)
    const fontSize = 120
    const fontFamily = "'Arial', 'Helvetica', sans-serif"
    ctx.font = `700 ${fontSize}px ${fontFamily}`
    ctx.textAlign = "center"
    ctx.textBaseline = "middle"

    const centerX = CANVAS_W / 2
    const lineHeight = fontSize * 1.4
    const baseY = CANVAS_H * 0.75

    // Line 1: partially "sung" (first half highlighted)
    const words1 = SAMPLE_LINE_1.split(" ")
    const midPoint = Math.ceil(words1.length / 2)
    const sungPart = words1.slice(0, midPoint).join(" ")
    const unsungPart = " " + words1.slice(midPoint).join(" ")

    // Measure widths for positioning
    const fullWidth1 = ctx.measureText(SAMPLE_LINE_1).width
    const sungWidth = ctx.measureText(sungPart).width
    const startX1 = centerX - fullWidth1 / 2

    // Draw sung portion
    ctx.fillStyle = SUNG_COLOR
    ctx.textAlign = "left"
    ctx.fillText(sungPart, startX1, baseY)

    // Draw unsung portion
    ctx.fillStyle = UNSUNG_COLOR
    ctx.fillText(unsungPart, startX1 + sungWidth, baseY)

    // Line 2: all unsung
    ctx.fillStyle = UNSUNG_COLOR
    ctx.textAlign = "center"
    ctx.fillText(SAMPLE_LINE_2, centerX, baseY + lineHeight)
  }, [backgroundUrl])

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
