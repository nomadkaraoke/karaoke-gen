'use client'

import { useEffect, useState } from 'react'
import Script from 'next/script'
import { isLocalPage } from '@/lib/local-host'

const GA_MEASUREMENT_ID = process.env.NEXT_PUBLIC_GA_MEASUREMENT_ID || 'G-D0E63LB9FY'

export function GoogleAnalytics() {
  // Decided after mount from the page's real host (static export has no server to ask), so a
  // local stack, nomad-mobile's E2E in an emulator's Chrome, or CI never sends hits to the real
  // property. Server and first client render agree (nothing), so hydration matches.
  const [enabled, setEnabled] = useState(false)
  useEffect(() => {
    setEnabled(!isLocalPage())
  }, [])

  if (!enabled) return null

  return (
    <>
      <Script
        src={`https://www.googletagmanager.com/gtag/js?id=${GA_MEASUREMENT_ID}`}
        strategy="afterInteractive"
      />
      <Script id="google-analytics" strategy="afterInteractive">
        {`
          window.dataLayer = window.dataLayer || [];
          function gtag(){dataLayer.push(arguments);}
          gtag('js', new Date());
          gtag('config', '${GA_MEASUREMENT_ID}');
        `}
      </Script>
    </>
  )
}
