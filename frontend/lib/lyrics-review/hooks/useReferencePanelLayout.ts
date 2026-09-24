'use client'

import { useCallback, useEffect, useRef, useState } from 'react'

const WIDTH_KEY = 'lyricsReviewReferenceWidthPx'
const COLLAPSED_KEY = 'lyricsReviewReferenceCollapsed'

/** Narrowest the Reference column can be dragged (px) — long lines wrap below their natural width. */
export const MIN_REFERENCE_WIDTH_PX = 160
/** Widest the Reference column can be dragged, as a fraction of the row. */
export const MAX_REFERENCE_WIDTH_FRACTION = 0.7

function readStorage(key: string): string | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage.getItem(key)
  } catch {
    return null
  }
}

function writeStorage(key: string, value: string | null) {
  try {
    if (value === null) window.localStorage.removeItem(key)
    else window.localStorage.setItem(key, value)
  } catch {
    // Private mode / quota — layout just won't persist.
  }
}

export function clampReferenceWidth(width: number, containerWidth: number): number {
  const max = Math.max(MIN_REFERENCE_WIDTH_PX, containerWidth * MAX_REFERENCE_WIDTH_FRACTION)
  return Math.round(Math.min(max, Math.max(MIN_REFERENCE_WIDTH_PX, width)))
}

/**
 * Reviewer-adjustable Synced/Reference split for the Waveforms view: a drag handle sets an
 * explicit Reference column width (null = the automatic fit-to-longest-line width), and a
 * toggle collapses the Reference column entirely. Both persist across reviews.
 */
export function useReferencePanelLayout() {
  const [widthPx, setWidthPx] = useState<number | null>(() => {
    const n = Number(readStorage(WIDTH_KEY))
    return Number.isFinite(n) && n > 0 ? n : null
  })
  const [collapsed, setCollapsed] = useState<boolean>(() => readStorage(COLLAPSED_KEY) === '1')
  const [dragging, setDragging] = useState(false)

  useEffect(() => writeStorage(WIDTH_KEY, widthPx === null ? null : String(widthPx)), [widthPx])
  useEffect(() => writeStorage(COLLAPSED_KEY, collapsed ? '1' : null), [collapsed])

  const toggleCollapsed = useCallback(() => setCollapsed((c) => !c), [])
  const resetWidth = useCallback(() => setWidthPx(null), [])

  // Active drag: the column's width + pointer x at press, and the row width for clamping.
  const dragRef = useRef<{ startX: number; startWidth: number; containerWidth: number } | null>(null)
  // Ends an in-flight drag (removes the window listeners); also run on unmount.
  const endDragRef = useRef<(() => void) | null>(null)
  useEffect(() => () => endDragRef.current?.(), [])

  /**
   * Pointer-down on the divider. `columnEl` is the Reference column (its current rendered
   * width seeds the drag, so the first drag starts from the auto width without a jump);
   * `containerEl` is the row holding both columns.
   */
  const startResize = useCallback(
    (e: React.PointerEvent, columnEl: HTMLElement | null, containerEl: HTMLElement | null) => {
      if (!columnEl || !containerEl) return
      e.preventDefault()
      dragRef.current = {
        startX: e.clientX,
        startWidth: columnEl.getBoundingClientRect().width,
        containerWidth: containerEl.getBoundingClientRect().width,
      }
      setDragging(true)

      const onMove = (ev: PointerEvent) => {
        const d = dragRef.current
        if (!d) return
        // The Reference column is on the right: dragging the divider left widens it.
        setWidthPx(clampReferenceWidth(d.startWidth - (ev.clientX - d.startX), d.containerWidth))
      }
      const onUp = () => {
        endDragRef.current = null
        dragRef.current = null
        setDragging(false)
        document.body.style.userSelect = ''
        document.body.style.cursor = ''
        window.removeEventListener('pointermove', onMove)
        window.removeEventListener('pointerup', onUp)
        window.removeEventListener('pointercancel', onUp)
        window.removeEventListener('blur', onUp)
      }
      document.body.style.userSelect = 'none'
      document.body.style.cursor = 'col-resize'
      window.addEventListener('pointermove', onMove)
      window.addEventListener('pointerup', onUp)
      window.addEventListener('pointercancel', onUp)
      // A lost pointerup (e.g. released outside the window / tab switch) must not leave
      // the page stuck in a resize with text selection disabled.
      window.addEventListener('blur', onUp)
      endDragRef.current = onUp
    },
    []
  )

  return { widthPx, setWidthPx, collapsed, toggleCollapsed, resetWidth, startResize, dragging }
}
