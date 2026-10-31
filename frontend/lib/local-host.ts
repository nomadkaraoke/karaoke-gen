/**
 * Hostnames that mean "this page is served by a local stack, not production": a developer's
 * `next dev`, gen's scripts/local-stack.sh, and nomad-mobile's E2E, which loads gen web in an
 * emulator's Chrome (127.0.0.1 through adb reverse, or 10.0.2.2, the Android emulator's alias for
 * the host machine). Pages on these hosts must never talk to production services: API calls go
 * relative (lib/api.ts API_BASE_URL) and Google Analytics is not loaded.
 */
export const LOCAL_HOSTNAMES: readonly string[] = ['localhost', '127.0.0.1', '10.0.2.2']

export function isLocalHostname(hostname: string | null | undefined): boolean {
  return !!hostname && LOCAL_HOSTNAMES.includes(hostname)
}

/** True in a browser whose page host is local. False during SSR / static export. */
export function isLocalPage(): boolean {
  return typeof window !== 'undefined' && isLocalHostname(window.location?.hostname)
}

/**
 * A local page's `?baseApiUrl=` override (pointing the page at another local stack, e.g. another E2E
 * lane's gen), only when it is an http(s) URL on a local host. Anything else is ignored (''), so a
 * crafted link can't send the page's bearer token to another origin.
 */
export function localApiOverride(search: string): string {
  const raw = new URLSearchParams(search).get('baseApiUrl')
  if (!raw) return ''
  try {
    const url = new URL(raw)
    return (url.protocol === 'http:' || url.protocol === 'https:') && isLocalHostname(url.hostname)
      ? raw.replace(/\/+$/, '')
      : ''
  } catch {
    return ''
  }
}
