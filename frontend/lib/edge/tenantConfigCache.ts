/**
 * Edge (Cloudflare Pages Function) cache for the tenant portal's config.
 *
 * Stale-while-revalidate: a cached config is served even when older than
 * FRESH_SECONDS, and refreshed in the background. A request only waits on the
 * backend when its colo has never cached the tenant, and then for at most
 * FETCH_TIMEOUT_MS (the client fetches the config itself if it isn't injected).
 *
 * Why (2026-10-13): with a plain 5-min TTL, a crawler loading 17 portal pages at
 * once missed the cache 17 times in the same second. That burst made Cloud Run
 * spawn 5 cold API instances, and the HTML responses waited 15-17s on them.
 */

export interface TenantConfigResponse {
  tenant: {
    id: string
    name: string
    subdomain: string
    is_active: boolean
    branding: {
      primary_color: string
      secondary_color: string
      accent_color: string | null
      background_color: string | null
      favicon_url: string | null
      site_title: string
    }
  } | null
  is_default: boolean
}

export const FRESH_SECONDS = 300 // refresh in the background after 5 minutes
export const STORE_SECONDS = 7 * 24 * 3600 // keep a stale copy for a week
export const FETCH_TIMEOUT_MS = 3000
const FETCHED_AT_HEADER = "X-Fetched-At"

interface EdgeCache {
  match(key: string): Promise<Response | undefined>
  put(key: string, response: Response): Promise<void>
}

export interface TenantConfigDeps {
  cache: EdgeCache
  fetchFn: typeof fetch
  waitUntil: (p: Promise<unknown>) => void
  now?: () => number
  apiBaseUrl?: string
}

/** Minimal shape check — a malformed 200 must not be cached for a week. */
function isTenantConfig(data: unknown): data is TenantConfigResponse {
  if (!data || typeof data !== "object") return false
  const d = data as { tenant?: unknown; is_default?: unknown }
  if (typeof d.is_default !== "boolean") return false
  if (d.tenant === null) return true
  const t = d.tenant as { id?: unknown; branding?: { primary_color?: unknown; secondary_color?: unknown } } | undefined
  return (
    !!t &&
    typeof t.id === "string" &&
    typeof t.branding?.primary_color === "string" &&
    typeof t.branding?.secondary_color === "string"
  )
}

// Concurrent misses inside one isolate share a single backend fetch.
const inFlight = new Map<string, Promise<TenantConfigResponse | null>>()

async function fetchAndStore(
  tenantId: string,
  cacheKey: string,
  deps: TenantConfigDeps
): Promise<TenantConfigResponse | null> {
  const existing = inFlight.get(cacheKey)
  if (existing) return existing

  const now = deps.now ?? Date.now
  const base = deps.apiBaseUrl ?? "https://api.nomadkaraoke.com"
  const p = (async () => {
    try {
      // Call unbound: Workers' fetch throws "Illegal invocation" when invoked as a
      // method (this === deps) — that broke every edge config fetch in v0.268.6.
      const fetchFn = deps.fetchFn
      const response = await fetchFn(
        `${base}/api/tenant/config?tenant=${encodeURIComponent(tenantId)}`,
        {
          headers: { "Content-Type": "application/json", "X-Tenant-ID": tenantId },
          signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
        }
      )
      if (!response.ok) {
        console.error(`Tenant config fetch failed: ${response.status}`)
        return null
      }
      const data: unknown = await response.json()
      if (!isTenantConfig(data)) {
        console.error("Tenant config fetch returned an unexpected payload")
        return null
      }
      await deps.cache.put(
        cacheKey,
        new Response(JSON.stringify(data), {
          headers: {
            "Content-Type": "application/json",
            "Cache-Control": `s-maxage=${STORE_SECONDS}`,
            [FETCHED_AT_HEADER]: String(now()),
          },
        })
      )
      return data
    } catch (err) {
      console.error("Failed to fetch tenant config:", err)
      return null
    } finally {
      inFlight.delete(cacheKey)
    }
  })()
  inFlight.set(cacheKey, p)
  return p
}

export async function getTenantConfig(
  tenantId: string,
  cacheKey: string,
  deps: TenantConfigDeps
): Promise<TenantConfigResponse | null> {
  const now = deps.now ?? Date.now
  const cached = await deps.cache.match(cacheKey)
  if (cached) {
    try {
      const fetchedAt = Number(cached.headers.get(FETCHED_AT_HEADER) || 0)
      const data: TenantConfigResponse = await cached.json()
      if (!fetchedAt || now() - fetchedAt > FRESH_SECONDS * 1000) {
        // Stale (or written before this header existed): serve it, refresh behind.
        deps.waitUntil(fetchAndStore(tenantId, cacheKey, deps))
      }
      return data
    } catch {
      // Corrupted cache entry — fall through to a fetch.
    }
  }
  return fetchAndStore(tenantId, cacheKey, deps)
}

/** Test helper: forget in-flight fetches between tests. */
export function _resetInFlightForTests(): void {
  inFlight.clear()
}
