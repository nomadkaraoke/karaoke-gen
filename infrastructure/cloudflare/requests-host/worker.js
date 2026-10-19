/**
 * requests-host: serves the song-request voting board ON requests.nomadkaraoke.com.
 *
 * The board is a static page in the gen Pages build (gen.nomadkaraoke.com/{locale}/requests/).
 * Before this Worker the vanity host 302-redirected there, so the address bar showed the
 * long gen URL and the short link couldn't be copied back out on mobile (Andrew pastes it
 * into YouTube comment replies). Now the short URL stays put:
 *
 *   /                      -> gen /{locale}/requests/ (query kept); locale = the board's
 *                             language-switcher cookie, else Accept-Language, else en
 *   /{locale} or /{locale}/ -> gen /{locale}/requests/ (explicit language, e.g. /es/)
 *   paths with an extension -> proxied from gen (/_next/* chunks, fonts, icons, sw.js, ...)
 *   any other page path    -> 302 to the same path on gen (the app lives there)
 *
 * Why a Worker and not a Pages custom domain + URL-rewrite rule: attaching the hostname to
 * the Pages project made Cloudflare serve it straight from Pages, bypassing the zone's
 * redirect rules (seen live 2026-10-19), and the Pulumi token can't edit Transform Rules.
 *
 * Deploy: ./deploy.sh (script upload). The route requests.nomadkaraoke.com/* is declared
 * in ../pages_workers.py.
 */

export const GEN_ORIGIN = "https://gen.nomadkaraoke.com"

// Mirror of frontend/i18n/routing.ts locales.
export const LOCALES = [
  "en", "es", "de", "pt", "fr", "ja", "ko", "zh", "it", "nl",
  "pl", "tr", "ru", "th", "id", "vi", "tl", "hi", "ar", "sv",
  "nb", "da", "fi", "cs", "ro", "hu", "el", "he", "ms", "uk",
  "hr", "sk", "ca",
]
const DEFAULT_LOCALE = "en"
// Set by the board's language switcher on this host (frontend/app/[locale]/requests/client.tsx).
export const LOCALE_COOKIE = "nk_requests_locale"

export function hasExtension(pathname) {
  const last = pathname.slice(pathname.lastIndexOf("/") + 1)
  return last.includes(".")
}

/**
 * First supported language in Accept-Language, by q-weight then order. Matches on the
 * primary subtag like the frontend's LocaleRedirect ("pt-BR" -> "pt"); "no"/"nn" -> "nb".
 */
export function pickLocale(acceptLanguage) {
  const prefs = (acceptLanguage || "")
    .split(",")
    .map((part, index) => {
      const [tag, ...params] = part.trim().split(";")
      const q = params.map((p) => p.trim()).find((p) => p.startsWith("q="))
      return { tag: tag.toLowerCase(), q: q ? Number(q.slice(2)) || 0 : 1, index }
    })
    .filter((p) => p.tag && p.q > 0)
    .sort((a, b) => b.q - a.q || a.index - b.index)
  for (const { tag } of prefs) {
    let prefix = tag.split("-")[0]
    if (prefix === "no" || prefix === "nn") prefix = "nb"
    if (LOCALES.includes(prefix)) return prefix
  }
  return DEFAULT_LOCALE
}

export function cookieLocale(cookieHeader) {
  for (const part of (cookieHeader || "").split(";")) {
    const [name, value] = part.trim().split("=")
    if (name === LOCALE_COOKIE && LOCALES.includes(value)) return value
  }
  return null
}

function serveBoard(request, locale, search) {
  return fetch(new Request(`${GEN_ORIGIN}/${locale}/requests/${search}`, request)).then((res) => {
    const out = new Response(res.body, res)
    out.headers.append("Vary", "Accept-Language, Cookie")
    return out
  })
}

export default {
  async fetch(request) {
    const url = new URL(request.url)

    if (url.pathname === "/") {
      const locale =
        cookieLocale(request.headers.get("cookie")) || pickLocale(request.headers.get("accept-language"))
      return serveBoard(request, locale, url.search)
    }

    const explicit = url.pathname.match(/^\/([a-z]{2})\/?$/)
    if (explicit && LOCALES.includes(explicit[1])) {
      return serveBoard(request, explicit[1], url.search)
    }

    if (hasExtension(url.pathname)) {
      return fetch(new Request(`${GEN_ORIGIN}${url.pathname}${url.search}`, request))
    }

    return Response.redirect(`${GEN_ORIGIN}${url.pathname}${url.search}`, 302)
  },
}
