'use client'

import { useEffect } from 'react'
import CrashReport from '@/components/CrashReport'

interface Props {
  error: Error & { digest?: string }
  reset: () => void
}

export default function LocaleError({ error, reset }: Props) {
  useEffect(() => {
    // The CrashReport component also auto-sends on mount; this log is just for
    // local dev visibility.
    if (typeof console !== 'undefined') {
      console.error('[error.tsx]', error)
    }
  }, [error])

  return <CrashReport error={error} source="error.tsx" digest={error.digest} onReset={reset} />
}
