/**
 * Cloudflare Pages Function for the tenant portal build.
 *
 * Runs at the edge before serving static files. For HTML responses:
 * 1. Detects tenant from hostname
 * 2. Fetches tenant config from backend API (with edge caching)
 * 3. Injects config as window.__TENANT_CONFIG__ so the frontend has it instantly
 * 4. Injects initial CSS variables, logo preload and title, and hides the
 *    prerendered default branding until the app applies the tenant's (no FOUC)
 *
 * Non-HTML requests (JS, CSS, images) pass through unmodified.
 */

import { getTenantConfig } from "../lib/edge/tenantConfigCache"
import { injectTenantIntoHtml } from "../lib/edge/tenantHtmlInjection"

/**
 * Extract tenant ID from hostname.
 * Supports: {tenant}.nomadkaraoke.com and {tenant}.gen.nomadkaraoke.com
 */
function extractTenantFromHost(hostname: string): string | null {
  const parts = hostname.toLowerCase().split(".")
  const nonTenantSubdomains = ["gen", "api", "www", "buy", "admin", "app", "beta", "requests"]

  // Accept exactly 3 parts (tenant.nomadkaraoke.com)
  // or exactly 4 parts where second is "gen" (tenant.gen.nomadkaraoke.com)
  const isThreePart = parts.length === 3 && parts[1] === "nomadkaraoke" && parts[2] === "com"
  const isFourPart = parts.length === 4 && parts[1] === "gen" && parts[2] === "nomadkaraoke" && parts[3] === "com"

  if ((isThreePart || isFourPart) && !nonTenantSubdomains.includes(parts[0])) {
    return parts[0]
  }

  return null
}

export const onRequest: PagesFunction = async (context) => {
  // Serve the static file first
  const response = await context.next()

  // Only modify HTML responses
  const contentType = response.headers.get("content-type") || ""
  if (!contentType.includes("text/html")) {
    return response
  }

  // Extract tenant from hostname
  const url = new URL(context.request.url)
  const tenantId = extractTenantFromHost(url.hostname)
  if (!tenantId) {
    return response
  }

  // Tenant config: edge-cached, stale-while-revalidate, bounded wait on a miss.
  const cacheKey = `${url.origin}/__tenant_config__/${tenantId}`
  const config = await getTenantConfig(tenantId, cacheKey, {
    cache: caches.default,
    fetchFn: (input, init) => fetch(input, init),
    waitUntil: (p) => context.waitUntil(p),
  })
  if (!config || !config.tenant) {
    return response
  }

  // Inject config, CSS variables, logo preload, title, and hide the prerendered
  // default branding until the app has applied the tenant's (no Nomad logo flash)
  const modifiedHtml = injectTenantIntoHtml(await response.text(), config)

  // Return modified response preserving headers
  const newHeaders = new Headers(response.headers)
  // Remove content-length since we modified the body
  newHeaders.delete("content-length")

  return new Response(modifiedHtml, {
    status: response.status,
    statusText: response.statusText,
    headers: newHeaders,
  })
}
