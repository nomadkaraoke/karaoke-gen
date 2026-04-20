'use client'

import { useEffect } from 'react'
import CrashReport from '@/components/CrashReport'

interface Props {
  error: Error & { digest?: string }
  reset: () => void
}

export default function RootError({ error, reset }: Props) {
  useEffect(() => {
    if (typeof console !== 'undefined') {
      console.error('[root error.tsx]', error)
    }
  }, [error])

  return <CrashReport error={error} source="error.tsx:root" digest={error.digest} onReset={reset} />
}
