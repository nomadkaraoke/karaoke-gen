/**
 * Frontend crash reporter.
 *
 * - Call `reportClientError(...)` from React error boundaries and global listeners.
 * - Dedupes identical errors within a short window to avoid spamming the API.
 * - Never throws; any failure to report is swallowed and logged to the console.
 */

import { API_BASE_URL } from '@/lib/api'

export interface ClientErrorContext {
  href: string
  userAgent: string
  innerWidth?: number
  innerHeight?: number
  locale?: string
  release?: string
  userEmail?: string | null
}

export interface CollectedContext {
  url: string
  userAgent: string
  locale: string
  release: string
  viewport: { w: number; h: number } | undefined
  userEmail: string | null
}

export interface ReportArgs {
  error: unknown
  /** Where the error was caught. e.g. 'window.onerror', 'error.tsx', 'CrashReportBoundary' */
  source: string
  context: ClientErrorContext
  extra?: Record<string, unknown>
}

const DEDUP_WINDOW_MS = 5_000
const recentSignatures = new Map<string, number>()

export function __resetForTest() {
  recentSignatures.clear()
}

function sanitizeUrl(href: string): string {
  if (!href) return ''
  try {
    const u = new URL(href)
    u.search = ''
    u.hash = ''
    return u.toString()
  } catch {
    return href.slice(0, 512)
  }
}

export function collectContext(ctx: ClientErrorContext): CollectedContext {
  const viewport =
    typeof ctx.innerWidth === 'number' && typeof ctx.innerHeight === 'number'
      ? { w: ctx.innerWidth, h: ctx.innerHeight }
      : undefined
  return {
    url: sanitizeUrl(ctx.href),
    userAgent: ctx.userAgent || '',
    locale: ctx.locale || 'en',
    release: ctx.release || process.env.NEXT_PUBLIC_COMMIT_SHA || '',
    viewport,
    userEmail: ctx.userEmail ?? null,
  }
}

function normalizeError(e: unknown): { message: string; stack: string | null } {
  if (e instanceof Error) {
    return {
      message: `${e.name}: ${e.message}`,
      stack: e.stack ?? null,
    }
  }
  if (typeof e === 'string') {
    return { message: e, stack: null }
  }
  try {
    return { message: JSON.stringify(e).slice(0, 4000), stack: null }
  } catch {
    return { message: String(e).slice(0, 4000), stack: null }
  }
}

function signatureFor(message: string, stack: string | null, source: string): string {
  return `${source}|${stack?.slice(0, 500) ?? message.slice(0, 500)}`
}

export async function reportClientError(args: ReportArgs): Promise<void> {
  try {
    const { message, stack } = normalizeError(args.error)
    const sig = signatureFor(message, stack, args.source)
    const now = Date.now()
    const last = recentSignatures.get(sig)
    if (last && now - last < DEDUP_WINDOW_MS) return
    recentSignatures.set(sig, now)
    // simple cleanup
    if (recentSignatures.size > 200) {
      for (const [k, t] of recentSignatures) {
        if (now - t > DEDUP_WINDOW_MS * 4) recentSignatures.delete(k)
      }
    }

    const ctx = collectContext(args.context)
    const body = {
      message,
      stack,
      url: ctx.url,
      user_agent: ctx.userAgent,
      release: ctx.release,
      user_email: ctx.userEmail,
      viewport: ctx.viewport ?? null,
      locale: ctx.locale,
      source: args.source,
      extra: args.extra ?? null,
    }

    await fetch(`${API_BASE_URL}/api/client-errors`, {
      method: 'POST',
      keepalive: true,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).catch(() => {
      /* swallow — never surface reporter errors to callers */
    })
  } catch (err) {
    if (typeof console !== 'undefined') {
      console.warn('[crash-reporter] failed:', err)
    }
  }
}
