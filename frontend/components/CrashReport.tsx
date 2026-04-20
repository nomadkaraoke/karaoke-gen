'use client'

import { useEffect, useMemo, useState } from 'react'
import { useTranslations } from 'next-intl'
import { AlertCircle, Check, Clipboard, RefreshCw, Send } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { useAuth } from '@/lib/auth'
import { reportClientError } from '@/lib/crash-reporter'
import { hardReload, isStale } from '@/lib/version-check'

type SendState = 'sending' | 'sent' | 'failed' | 'idle'

interface Props {
  error: unknown
  source: string
  /** Optional Next.js error digest (present for errors in server components) */
  digest?: string
  /** Optional reset handler (present for Next.js error.tsx boundaries) */
  onReset?: () => void
  /** Optional "back" URL for the in-boundary version */
  backHref?: string
}

function errorText(error: unknown): { name: string; message: string; stack: string } {
  if (error instanceof Error) {
    return { name: error.name, message: error.message, stack: error.stack ?? '' }
  }
  if (typeof error === 'string') return { name: 'Error', message: error, stack: '' }
  try {
    return { name: 'Error', message: JSON.stringify(error), stack: '' }
  } catch {
    return { name: 'Error', message: String(error), stack: '' }
  }
}

export default function CrashReport({ error, source, digest, onReset, backHref }: Props) {
  const t = useTranslations('crashReport')
  const user = useAuth((s) => s.user)
  const [state, setState] = useState<SendState>('idle')
  const [copied, setCopied] = useState(false)
  const [showStack, setShowStack] = useState(false)
  const [staleInfo, setStaleInfo] = useState<{ latestSha: string } | null>(null)

  useEffect(() => {
    let cancelled = false
    void isStale().then((r) => {
      if (!cancelled && r.stale) setStaleInfo({ latestSha: r.latestSha })
    })
    return () => {
      cancelled = true
    }
  }, [])

  const info = useMemo(() => errorText(error), [error])

  const debugText = useMemo(() => {
    const lines = [
      `${info.name}: ${info.message}`,
      digest ? `Digest: ${digest}` : null,
      `Source: ${source}`,
      `URL: ${typeof window !== 'undefined' ? window.location.href : ''}`,
      `UA: ${typeof navigator !== 'undefined' ? navigator.userAgent : ''}`,
      `Build: ${process.env.NEXT_PUBLIC_COMMIT_SHA || '(dev)'}`,
      `When: ${new Date().toISOString()}`,
      '',
      info.stack || '(no stack)',
    ].filter(Boolean) as string[]
    return lines.join('\n')
  }, [info, source, digest])

  async function send() {
    setState('sending')
    try {
      await reportClientError({
        error,
        source,
        context: {
          href: typeof window !== 'undefined' ? window.location.href : '',
          userAgent: typeof navigator !== 'undefined' ? navigator.userAgent : '',
          innerWidth: typeof window !== 'undefined' ? window.innerWidth : undefined,
          innerHeight: typeof window !== 'undefined' ? window.innerHeight : undefined,
          userEmail: user?.email ?? null,
        },
        extra: { digest, interactive: state === 'idle' ? 'auto' : 'manual', latest_sha: staleInfo?.latestSha ?? null },
      })
      setState('sent')
    } catch {
      setState('failed')
    }
  }

  useEffect(() => {
    // auto-send once on mount
    void send()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function copy() {
    try {
      await navigator.clipboard.writeText(debugText)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      /* ignore */
    }
  }

  return (
    <div className="flex min-h-[60vh] items-center justify-center p-4">
      <div className="w-full max-w-xl rounded-xl border bg-card p-6 shadow-sm">
        <div className="flex items-start gap-3">
          <AlertCircle className="mt-0.5 h-6 w-6 shrink-0 text-destructive" />
          <div className="flex-1 space-y-1">
            <h1 className="text-lg font-semibold">{t('title')}</h1>
            <p className="text-sm text-muted-foreground">{t('subtitle')}</p>
          </div>
        </div>

        {staleInfo && (
          <div className="mt-4 rounded-md border border-amber-400/60 bg-amber-400/10 p-3 text-sm" data-testid="crash-report-stale-banner">
            <strong className="font-medium">{t('updateAvailable')}</strong>
            <p className="mt-1 text-xs text-muted-foreground">{t('updateAvailableSubtitle')}</p>
            <Button
              size="sm"
              className="mt-2 min-h-[40px]"
              onClick={() => hardReload(staleInfo.latestSha)}
            >
              {t('updateNow')}
            </Button>
          </div>
        )}

        <div
          className="mt-4 rounded-md border bg-muted/40 p-3 text-xs font-mono break-words"
          data-testid="crash-report-message"
        >
          <strong>{info.name}:</strong> {info.message}
        </div>

        <div className="mt-3">
          <button
            type="button"
            className="text-xs text-muted-foreground underline"
            onClick={() => setShowStack((v) => !v)}
          >
            {showStack ? t('hideStack') : t('toggleStack')}
          </button>
          {showStack && (
            <pre className="mt-2 max-h-64 overflow-auto rounded-md border bg-muted/40 p-3 text-[10px] leading-tight">
              {debugText}
            </pre>
          )}
        </div>

        <div
          className="mt-4 text-xs"
          role="status"
          data-testid="crash-report-status"
        >
          {state === 'sending' && <span>{t('reportSending')}</span>}
          {state === 'sent' && (
            <span className="inline-flex items-center gap-1 text-emerald-600">
              <Check className="h-3.5 w-3.5" />
              {t('reportSent')}
            </span>
          )}
          {state === 'failed' && (
            <span className="text-destructive">{t('reportFailed')}</span>
          )}
        </div>

        <div className="mt-4 flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={copy} className="min-h-[40px]">
            <Clipboard className="mr-1.5 h-4 w-4" />
            {copied ? t('copied') : t('copyDebug')}
          </Button>
          <Button variant="outline" size="sm" onClick={send} disabled={state === 'sending'} className="min-h-[40px]">
            <Send className="mr-1.5 h-4 w-4" />
            {t('sendReport')}
          </Button>
          {onReset && (
            <Button
              size="sm"
              variant={staleInfo ? 'outline' : 'default'}
              onClick={onReset}
              className="min-h-[40px]"
            >
              <RefreshCw className="mr-1.5 h-4 w-4" />
              {t('retry')}
            </Button>
          )}
          {backHref && (
            <Button variant="ghost" size="sm" asChild className="min-h-[40px]">
              <a href={backHref}>{t('backToDashboard')}</a>
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}
