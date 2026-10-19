/**
 * requests-host: serves the song-request voting board ON requests.nomadkaraoke.com.
 *
 * The board is a static page in the gen Pages build (gen.nomadkaraoke.com/en/requests/).
 * Before this Worker the vanity host 302-redirected there, so the address bar showed the
 * long gen URL and the short link couldn't be copied back out on mobile (Andrew pastes it
 * into YouTube comment replies). Now the short URL stays put:
 *
 *   /                      -> gen /en/requests/ fetched and returned as-is (query kept)
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
const BOARD_PATH = "/en/requests/"

export function hasExtension(pathname) {
  const last = pathname.slice(pathname.lastIndexOf("/") + 1)
  return last.includes(".")
}

export default {
  async fetch(request) {
    const url = new URL(request.url)

    if (url.pathname === "/") {
      return fetch(new Request(`${GEN_ORIGIN}${BOARD_PATH}${url.search}`, request))
    }

    if (hasExtension(url.pathname)) {
      return fetch(new Request(`${GEN_ORIGIN}${url.pathname}${url.search}`, request))
    }

    return Response.redirect(`${GEN_ORIGIN}${url.pathname}${url.search}`, 302)
  },
}
