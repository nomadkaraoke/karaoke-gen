import Link from "next/link"
import { LocaleRedirect } from "@/components/LocaleRedirect"
import enMessages from "@/messages/en.json"

// This URL is linked from outreach emails, so it must degrade gracefully
// without JavaScript: <noscript> meta-refresh plus a visible fallback link.
export default function KaraokeHuntNewsPage() {
  return (
    <>
      <noscript>
        <meta httpEquiv="refresh" content="0;url=/en/karaokehuntnews" />
      </noscript>
      <LocaleRedirect />
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Link href="/en/karaokehuntnews" style={{ textDecoration: 'underline' }}>
          {enMessages.karaokehuntNewsLetter.title} →
        </Link>
      </div>
    </>
  )
}
