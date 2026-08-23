"use client"

/**
 * LocaleBadge — small flag + language pill for admin views.
 *
 * Shows the UI language a user/job was using the product in, so support/intervention
 * can communicate in the right language. Admin-only: never rendered in the customer UI.
 *
 * `locale` is a primary language subtag (any of the 33 supported UI locales, e.g.
 * "pt", "ja", "zh"). The flag is a representative country for that language (decorative);
 * the language name comes from Intl.DisplayNames.
 */
import { Badge } from "@/components/ui/badge"
import { countryCodeToFlag } from "@/lib/ip-geolocation"

// Representative country flag per supported language subtag (decorative only).
const LOCALE_TO_COUNTRY: Record<string, string> = {
  en: "GB", es: "ES", de: "DE", pt: "PT", fr: "FR", ja: "JP", ko: "KR",
  zh: "CN", it: "IT", nl: "NL", pl: "PL", tr: "TR", ru: "RU", th: "TH",
  id: "ID", vi: "VN", tl: "PH", hi: "IN", ar: "SA", sv: "SE", nb: "NO",
  da: "DK", fi: "FI", cs: "CZ", ro: "RO", hu: "HU", el: "GR", he: "IL",
  ms: "MY", uk: "UA", hr: "HR", sk: "SK", ca: "ES",
}

function languageName(locale: string): string {
  try {
    const dn = new Intl.DisplayNames(["en"], { type: "language" })
    return dn.of(locale) || locale
  } catch {
    return locale
  }
}

export function localeDisplayName(locale?: string | null): string | null {
  if (!locale) return null
  return languageName(locale.toLowerCase())
}

interface LocaleBadgeProps {
  locale?: string | null
  /** Show the full language name instead of just the code (for detail views). */
  showName?: boolean
  /** Render a muted placeholder when no locale is known (default: render nothing). */
  showEmpty?: boolean
  className?: string
}

export function LocaleBadge({ locale, showName = false, showEmpty = false, className }: LocaleBadgeProps) {
  const normalized = locale ? locale.toLowerCase().split("-")[0] : ""

  if (!normalized) {
    if (!showEmpty) return null
    return (
      <Badge variant="outline" className={className} title="Unknown language">
        🌐 —
      </Badge>
    )
  }

  const flag = countryCodeToFlag(LOCALE_TO_COUNTRY[normalized] || "")
  const name = languageName(normalized)
  const label = showName ? name : normalized

  return (
    <Badge variant="secondary" className={className} title={`Language: ${name} (${normalized})`}>
      {flag ? `${flag} ` : ""}{label}
    </Badge>
  )
}
