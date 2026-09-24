import { act, renderHook } from '@testing-library/react'
import {
  clampReferenceWidth,
  MIN_REFERENCE_WIDTH_PX,
  useReferencePanelLayout,
} from '../useReferencePanelLayout'

function el(width: number): HTMLElement {
  const div = document.createElement('div')
  div.getBoundingClientRect = () => ({ width } as DOMRect)
  return div
}

describe('clampReferenceWidth', () => {
  it('keeps widths within [min, 70% of the row]', () => {
    expect(clampReferenceWidth(50, 1000)).toBe(MIN_REFERENCE_WIDTH_PX)
    expect(clampReferenceWidth(900, 1000)).toBe(700)
    expect(clampReferenceWidth(333.4, 1000)).toBe(333)
  })

  it('never goes below the minimum even in a very narrow row', () => {
    expect(clampReferenceWidth(500, 100)).toBe(MIN_REFERENCE_WIDTH_PX)
  })
})

describe('useReferencePanelLayout', () => {
  beforeEach(() => window.localStorage.clear())

  it('defaults to the automatic width, expanded', () => {
    const { result } = renderHook(() => useReferencePanelLayout())
    expect(result.current.widthPx).toBeNull()
    expect(result.current.collapsed).toBe(false)
  })

  it('toggles collapse and persists it', () => {
    const { result, unmount } = renderHook(() => useReferencePanelLayout())
    act(() => result.current.toggleCollapsed())
    expect(result.current.collapsed).toBe(true)
    unmount()
    const { result: again } = renderHook(() => useReferencePanelLayout())
    expect(again.current.collapsed).toBe(true)
    act(() => again.current.toggleCollapsed())
    expect(window.localStorage.getItem('lyricsReviewReferenceCollapsed')).toBeNull()
  })

  it('dragging the divider left widens the Reference column (starting from its rendered width)', () => {
    const { result, unmount } = renderHook(() => useReferencePanelLayout())
    const column = el(300)
    const row = el(1200)

    act(() => {
      result.current.startResize({ clientX: 800, preventDefault() {} } as any, column, row)
    })
    expect(result.current.dragging).toBe(true)

    act(() => {
      window.dispatchEvent(new MouseEvent('pointermove', { clientX: 700 }) as PointerEvent)
    })
    expect(result.current.widthPx).toBe(400)

    // Dragging right past the minimum clamps (lines wrap rather than vanish).
    act(() => {
      window.dispatchEvent(new MouseEvent('pointermove', { clientX: 1100 }) as PointerEvent)
    })
    expect(result.current.widthPx).toBe(MIN_REFERENCE_WIDTH_PX)

    act(() => {
      window.dispatchEvent(new MouseEvent('pointerup'))
    })
    expect(result.current.dragging).toBe(false)

    // Further moves after release are ignored.
    act(() => {
      window.dispatchEvent(new MouseEvent('pointermove', { clientX: 0 }) as PointerEvent)
    })
    expect(result.current.widthPx).toBe(MIN_REFERENCE_WIDTH_PX)

    unmount()
    const { result: again } = renderHook(() => useReferencePanelLayout())
    expect(again.current.widthPx).toBe(MIN_REFERENCE_WIDTH_PX)
    act(() => again.current.resetWidth())
    expect(again.current.widthPx).toBeNull()
    expect(window.localStorage.getItem('lyricsReviewReferenceWidthPx')).toBeNull()
  })
})
