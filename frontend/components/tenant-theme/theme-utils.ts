import type { ThemeStyleParams } from "@/lib/api"

/** Production render defaults when a karaoke key is absent (see OutputGenerator). */
export const KARAOKE_DEFAULTS = {
  font_size: 250,
  max_line_length: 36,
} as const

export const FRAME_W = 3840
export const FRAME_H = 2160

/** "r, g, b, a" (ASS colour in karaoke styles) → "#rrggbb" (alpha dropped). */
export function rgbaToHex(value: unknown, fallback = "#ffffff"): string {
  if (typeof value !== "string") return fallback
  const parts = value.split(",").map((p) => parseInt(p.trim(), 10))
  if (parts.length < 3 || parts.slice(0, 3).some((n) => Number.isNaN(n))) return fallback
  return "#" + parts.slice(0, 3).map((n) => Math.max(0, Math.min(255, n)).toString(16).padStart(2, "0")).join("")
}

/** "#rrggbb" → "r, g, b, a" keeping the previous alpha (default 255). */
export function hexToRgba(hex: string, previous?: unknown): string {
  const m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex.trim())
  if (!m) return typeof previous === "string" ? previous : "255, 255, 255, 255"
  let alpha = 255
  if (typeof previous === "string") {
    const a = parseInt(previous.split(",")[3] ?? "", 10)
    if (!Number.isNaN(a)) alpha = a
  }
  return `${parseInt(m[1], 16)}, ${parseInt(m[2], 16)}, ${parseInt(m[3], 16)}, ${alpha}`
}

export interface Region {
  x: number
  y: number
  w: number
  h: number
}

export function parseRegion(value: unknown, fallback: Region): Region {
  if (typeof value !== "string") return fallback
  const parts = value.split(",").map((p) => Math.round(parseFloat(p)))
  if (parts.length !== 4 || parts.some((n) => Number.isNaN(n))) return fallback
  const [x, y, w, h] = parts
  return { x, y, w, h }
}

export function formatRegion(r: Region): string {
  // Keep inside the 4K frame (the backend rejects regions that overflow).
  const h = Math.max(20, Math.min(r.h, FRAME_H))
  const y = Math.max(0, Math.min(r.y, FRAME_H - h))
  const w = Math.max(20, Math.min(r.w, FRAME_W))
  const x = Math.max(0, Math.min(r.x, FRAME_W - w))
  return `${x},${y},${w},${h}`
}

/** Immutable set of style_params[section][field]. */
export function setField(styles: ThemeStyleParams, section: string, field: string, value: unknown): ThemeStyleParams {
  return { ...styles, [section]: { ...(styles[section] ?? {}), [field]: value } }
}

export function getField<T = unknown>(styles: ThemeStyleParams, section: string, field: string): T | undefined {
  return styles?.[section]?.[field] as T | undefined
}

/** Stable JSON used to detect unsaved changes. */
export function stableStringify(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`
  if (value && typeof value === "object") {
    return `{${Object.keys(value as Record<string, unknown>)
      .sort()
      .map((k) => `${JSON.stringify(k)}:${stableStringify((value as Record<string, unknown>)[k])}`)
      .join(",")}}`
  }
  return JSON.stringify(value)
}
