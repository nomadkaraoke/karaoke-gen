import Link from "next/link"
import { LocaleRedirect } from "@/components/LocaleRedirect"
import enMessages from "@/messages/en.json"

// This URL is linked from outreach emails, so it must degrade gracefully
// without JavaScript: <noscript> meta-refresh plus a visible fallback link.
export default function KaraokeHuntListPage() {
  return (
    <>
      <noscript>
        <meta httpEquiv="refresh" content="0;url=/en/karaokehuntlist" />
      </noscript>
      <LocaleRedirect />
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Link href="/en/karaokehuntlist" style={{ textDecoration: 'underline' }}>
          {enMessages.karaokehuntListLetter.title} →
        </Link>
      </div>
    </>
  )
}
