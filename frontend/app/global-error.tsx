'use client'

import { useEffect } from 'react'
import { reportClientError } from '@/lib/crash-reporter'

interface Props {
  error: Error & { digest?: string }
  reset: () => void
}

export default function GlobalError({ error, reset }: Props) {
  useEffect(() => {
    void reportClientError({
      error,
      source: 'global-error.tsx:root',
      context: {
        href: typeof window !== 'undefined' ? window.location.href : '',
        userAgent: typeof navigator !== 'undefined' ? navigator.userAgent : '',
        innerWidth: typeof window !== 'undefined' ? window.innerWidth : undefined,
        innerHeight: typeof window !== 'undefined' ? window.innerHeight : undefined,
        userEmail: null,
      },
      extra: { digest: error.digest },
    })
  }, [error])

  return (
    <html lang="en">
      <body>
        <div style={{ minHeight: '60vh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16, fontFamily: 'system-ui, sans-serif' }}>
          <div style={{ maxWidth: 560 }}>
            <h1 style={{ fontSize: 20, marginBottom: 8 }}>Something went wrong</h1>
            <p style={{ color: '#666', fontSize: 14 }}>
              The app hit an unexpected error. We&apos;ve been notified automatically.
            </p>
            <pre style={{ marginTop: 12, padding: 12, background: '#f4f4f4', border: '1px solid #e5e5e5', borderRadius: 6, fontSize: 11, overflow: 'auto' }}>
              {error.name}: {error.message}
              {error.digest ? `\nDigest: ${error.digest}` : ''}
            </pre>
            <button onClick={reset} style={{ marginTop: 12, padding: '8px 16px', border: '1px solid #ddd', borderRadius: 6, background: '#fff', cursor: 'pointer' }}>
              Try again
            </button>
          </div>
        </div>
      </body>
    </html>
  )
}
