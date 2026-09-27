/**
 * Client-side diagnostics attached to crash reports.
 *
 * Some failures (e.g. Firefox "out of memory" after a long session) arrive with
 * no Error object and no useful stack, so the report alone can't say what the
 * page was doing. We keep a small ring buffer of breadcrumbs (navigation,
 * clicks, app events such as upload phases) and snapshot resource usage at
 * report time so the alert carries enough context to narrow the cause down.
 *
 * Everything here is best-effort and must never throw.
 */

export interface Breadcrumb {
  /** Seconds since page load */
  t: number
  category: string
  message: string
}

const MAX_BREADCRUMBS = 30
const MAX_MESSAGE_CHARS = 120

const breadcrumbs: Breadcrumb[] = []
let installed = false
let liveBlobUrls = 0

function secondsSinceLoad(): number {
  if (typeof performance === 'undefined') return 0
  return Math.round(performance.now() / 100) / 10
}

export function addBreadcrumb(category: string, message: string): void {
  try {
    breadcrumbs.push({ t: secondsSinceLoad(), category, message: message.slice(0, MAX_MESSAGE_CHARS) })
    if (breadcrumbs.length > MAX_BREADCRUMBS) breadcrumbs.splice(0, breadcrumbs.length - MAX_BREADCRUMBS)
  } catch {
    /* never throw */
  }
}

export function getBreadcrumbs(): Breadcrumb[] {
  return breadcrumbs.slice()
}

/** Short human label for a clicked element: role/tag + accessible text. */
function describeElement(el: Element | null): string | null {
  const target = el?.closest?.('button, a, [role="button"], [role="tab"], input, select, label')
  if (!target) return null
  const label =
    target.getAttribute('aria-label') ||
    target.getAttribute('data-testid') ||
    (target as HTMLElement).innerText ||
    target.getAttribute('name') ||
    ''
  return `${target.tagName.toLowerCase()} "${label.replace(/\s+/g, ' ').trim().slice(0, 60)}"`
}

/** Install breadcrumb collectors once per page. Safe to call repeatedly. */
export function installDiagnostics(): void {
  if (installed || typeof window === 'undefined') return
  installed = true
  try {
    addBreadcrumb('nav', `load ${window.location.pathname}${window.location.hash}`)

    // Route changes (Next.js client navigation uses history.pushState/replaceState).
    for (const method of ['pushState', 'replaceState'] as const) {
      const original = window.history[method]
      window.history[method] = function (this: History, ...args: Parameters<History['pushState']>) {
        const result = original.apply(this, args)
        addBreadcrumb('nav', `${method} ${window.location.pathname}${window.location.hash}`)
        return result
      } as History['pushState']
    }
    window.addEventListener('popstate', () => addBreadcrumb('nav', `popstate ${window.location.pathname}`))
    window.addEventListener('hashchange', () => addBreadcrumb('nav', `hash ${window.location.hash}`))
    document.addEventListener('visibilitychange', () => addBreadcrumb('page', `visibility ${document.visibilityState}`))

    document.addEventListener(
      'click',
      (e) => {
        const label = describeElement(e.target as Element)
        if (label) addBreadcrumb('click', label)
      },
      { capture: true, passive: true },
    )

    // Count outstanding blob: URLs — a leak here (e.g. audio previews of large
    // files never revoked) holds whole files in memory.
    if (typeof URL !== 'undefined' && typeof URL.createObjectURL === 'function') {
      const create = URL.createObjectURL.bind(URL)
      const revoke = URL.revokeObjectURL.bind(URL)
      URL.createObjectURL = (obj: Blob | MediaSource) => {
        liveBlobUrls++
        return create(obj)
      }
      URL.revokeObjectURL = (url: string) => {
        liveBlobUrls = Math.max(0, liveBlobUrls - 1)
        return revoke(url)
      }
    }
  } catch {
    /* never throw */
  }
}

export interface DiagnosticsSnapshot {
  page_age_s: number
  visibility?: string
  online?: boolean
  device_memory_gb?: number
  hardware_concurrency?: number
  /** Chromium only (performance.memory) */
  js_heap_used_mb?: number
  js_heap_limit_mb?: number
  dom_nodes?: number
  audio_elements?: number
  video_elements?: number
  canvas_elements?: number
  live_blob_urls: number
}

export function collectDiagnostics(): DiagnosticsSnapshot {
  const snap: DiagnosticsSnapshot = { page_age_s: Math.round(secondsSinceLoad()), live_blob_urls: liveBlobUrls }
  try {
    if (typeof document !== 'undefined') {
      snap.visibility = document.visibilityState
      snap.dom_nodes = document.getElementsByTagName('*').length
      snap.audio_elements = document.getElementsByTagName('audio').length
      snap.video_elements = document.getElementsByTagName('video').length
      snap.canvas_elements = document.getElementsByTagName('canvas').length
    }
    if (typeof navigator !== 'undefined') {
      snap.online = navigator.onLine
      const nav = navigator as Navigator & { deviceMemory?: number }
      if (typeof nav.deviceMemory === 'number') snap.device_memory_gb = nav.deviceMemory
      if (typeof navigator.hardwareConcurrency === 'number') snap.hardware_concurrency = navigator.hardwareConcurrency
    }
    const mem = (typeof performance !== 'undefined'
      ? (performance as Performance & { memory?: { usedJSHeapSize: number; jsHeapSizeLimit: number } }).memory
      : undefined)
    if (mem) {
      snap.js_heap_used_mb = Math.round(mem.usedJSHeapSize / 1048576)
      snap.js_heap_limit_mb = Math.round(mem.jsHeapSizeLimit / 1048576)
    }
  } catch {
    /* partial snapshot is fine */
  }
  return snap
}

export function __resetDiagnosticsForTest(): void {
  breadcrumbs.length = 0
  liveBlobUrls = 0
}
