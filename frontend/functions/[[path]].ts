/**
 * Cloudflare Pages Function for the tenant portal build.
 *
 * Runs at the edge before serving static files. For HTML responses:
 * 1. Detects tenant from hostname
 * 2. Fetches tenant config from backend API (with edge caching)
 * 3. Injects config as window.__TENANT_CONFIG__ so the frontend has it instantly
 * 4. Injects initial CSS variables for instant branding (no FOUC)
 *
 * Non-HTML requests (JS, CSS, images) pass through unmodified.
 */

interface TenantBranding {
  primary_color: string
  secondary_color: string
  accent_color: string | null
  background_color: string | null
  favicon_url: string | null
  site_title: string
}

interface TenantPublicConfig {
  id: string
  name: string
  subdomain: string
  is_active: boolean
  branding: TenantBranding
}

interface TenantConfigResponse {
  tenant: TenantPublicConfig | null
  is_default: boolean
}

const API_BASE_URL = "https://api.nomadkaraoke.com"
const CACHE_TTL_SECONDS = 300 // 5 minutes

/**
 * Extract tenant ID from hostname.
 * Supports: {tenant}.nomadkaraoke.com and {tenant}.gen.nomadkaraoke.com
 */
function extractTenantFromHost(hostname: string): string | null {
  const parts = hostname.toLowerCase().split(".")
  const nonTenantSubdomains = ["gen", "api", "www", "buy", "admin", "app", "beta"]

  // Accept exactly 3 parts (tenant.nomadkaraoke.com)
  // or exactly 4 parts where second is "gen" (tenant.gen.nomadkaraoke.com)
  const isThreePart = parts.length === 3 && parts[1] === "nomadkaraoke" && parts[2] === "com"
  const isFourPart = parts.length === 4 && parts[1] === "gen" && parts[2] === "nomadkaraoke" && parts[3] === "com"

  if ((isThreePart || isFourPart) && !nonTenantSubdomains.includes(parts[0])) {
    return parts[0]
  }

  return null
}

/**
 * Fetch tenant config from backend with Cloudflare Cache API caching.
 */
async function fetchTenantConfigCached(
  tenantId: string,
  cacheKey: string
): Promise<TenantConfigResponse | null> {
  // Try cache first
  const cache = caches.default
  const cachedResponse = await cache.match(cacheKey)
  if (cachedResponse) {
    try {
      return await cachedResponse.json()
    } catch {
      // Cache corrupted, proceed to fetch
    }
  }

  // Fetch from backend
  try {
    const response = await fetch(
      `${API_BASE_URL}/api/tenant/config?tenant=${encodeURIComponent(tenantId)}`,
      {
        headers: {
          "Content-Type": "application/json",
          "X-Tenant-ID": tenantId,
        },
      }
    )

    if (!response.ok) {
      console.error(`Tenant config fetch failed: ${response.status}`)
      return null
    }

    const data: TenantConfigResponse = await response.json()

    // Cache the response
    const cacheResponse = new Response(JSON.stringify(data), {
      headers: {
        "Content-Type": "application/json",
        "Cache-Control": `s-maxage=${CACHE_TTL_SECONDS}`,
      },
    })
    // Don't await — fire-and-forget cache put
    cache.put(cacheKey, cacheResponse)

    return data
  } catch (err) {
    console.error("Failed to fetch tenant config:", err)
    return null
  }
}

/**
 * Escape a string for safe injection into an HTML <script> tag.
 * Prevents XSS via </script> or <!-- injection.
 */
function escapeForScript(json: string): string {
  return json
    .replace(/</g, "\\u003c")
    .replace(/>/g, "\\u003e")
    .replace(/&/g, "\\u0026")
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

  // Fetch tenant config (cached at edge)
  const cacheKey = `${url.origin}/__tenant_config__/${tenantId}`
  const config = await fetchTenantConfigCached(tenantId, cacheKey)
  if (!config || !config.tenant) {
    return response
  }

  // Build injection: config script + CSS variables for instant branding
  const configJson = escapeForScript(JSON.stringify(config))
  const branding = config.tenant.branding
  const cssVars = [
    `--tenant-primary:${branding.primary_color}`,
    `--tenant-secondary:${branding.secondary_color}`,
  ]
  if (branding.accent_color) {
    cssVars.push(`--tenant-accent:${branding.accent_color}`)
  }
  if (branding.background_color) {
    cssVars.push(`--tenant-background:${branding.background_color}`)
  }

  const injection = [
    `<script>window.__TENANT_CONFIG__=${configJson};</script>`,
    `<style>:root{${cssVars.join(";")}}</style>`,
  ].join("\n")

  // Inject before </head>
  const html = await response.text()
  const modifiedHtml = html.replace("</head>", injection + "\n</head>")

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
