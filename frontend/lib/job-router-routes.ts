/**
 * URL parsers for the JobRouterClient.
 *
 * Two routing modes share this module:
 *  - Local CLI mode: path-based, `[/{locale}]/app/jobs/{jobId}/{action}`.
 *    The optional `/{locale}/` prefix is added by `LocaleRedirect` whenever
 *    the user lands on a legacy non-locale path, so the parser must accept
 *    both shapes.
 *  - Cloud mode: hash-based, `#/{jobId}/{action}`.
 */

export type RouteType = "review" | "instrumental" | "audio-edit" | "unknown"

export interface ParsedRoute {
  jobId: string | null
  routeType: RouteType
}

// The optional `(?:[a-z]{2}\/)?` slot accepts the `LocaleRedirect`-added
// `/{locale}/` prefix. Without it, navigating from /app/jobs/local/review
// to /app/jobs/local/instrumental ends up at /en/app/jobs/local/instrumental
// (LocaleRedirect bounces non-locale paths) and the parser would fall
// through to the default "review" routeType — silently rendering the
// lyrics review UI on the instrumental URL.
const PATHNAME_RE =
  /^\/(?:[a-z]{2}\/)?app\/jobs\/([^/]+)\/(review|instrumental|audio-edit)\/?$/

const HASH_RE = /^\/?([^/]+)\/(review|instrumental|audio-edit)\/?$/

/** Parse `/[locale]/app/jobs/{jobId}/{action}` (local mode). */
export function parseRouteFromPathname(pathname: string): ParsedRoute {
  if (!pathname) return { jobId: null, routeType: "unknown" }
  const match = pathname.match(PATHNAME_RE)
  if (!match) return { jobId: null, routeType: "unknown" }
  const [, jobId, action] = match
  return { jobId, routeType: action as RouteType }
}

/** Parse `#/{jobId}/{action}` (cloud mode). */
export function parseRouteFromHash(hash: string): ParsedRoute {
  if (!hash || hash.length <= 1) return { jobId: null, routeType: "unknown" }
  const match = hash.substring(1).match(HASH_RE)
  if (!match) return { jobId: null, routeType: "unknown" }
  const [, jobId, action] = match
  return { jobId, routeType: action as RouteType }
}
