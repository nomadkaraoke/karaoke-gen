import Link from "next/link"
import { LocaleRedirect } from "@/components/LocaleRedirect"

// This URL is linked from outreach emails, so it must degrade gracefully
// without JavaScript: <noscript> meta-refresh plus a visible fallback link.
export default function KaraokeHuntPage() {
  return (
    <>
      <noscript>
        <meta httpEquiv="refresh" content="0;url=/en/karaokehunt" />
      </noscript>
      <LocaleRedirect />
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Link href="/en/karaokehunt" style={{ textDecoration: 'underline' }}>
          A note for KaraokeHunt users →
        </Link>
      </div>
    </>
  )
}
