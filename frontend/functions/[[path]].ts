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

import { getTenantConfig } from "../lib/edge/tenantConfigCache"

/**
 * Compute a contrasting foreground color (black or white) for a given hex color.
 * Uses WCAG relative luminance to determine which provides better contrast.
 */
function getContrastColor(hexColor: string): string {
  const hex = hexColor.replace("#", "")
  const r = parseInt(hex.slice(0, 2), 16) / 255
  const g = parseInt(hex.slice(2, 4), 16) / 255
  const b = parseInt(hex.slice(4, 6), 16) / 255

  const toLinear = (c: number) => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4))
  const luminance = 0.2126 * toLinear(r) + 0.7152 * toLinear(g) + 0.0722 * toLinear(b)

  return luminance > 0.179 ? "#000000" : "#ffffff"
}

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

  // Build injection: config script + CSS variables for instant branding
  const configJson = escapeForScript(JSON.stringify(config))
  const branding = config.tenant.branding
  const cssVars = [
    `--tenant-primary:${branding.primary_color}`,
    `--tenant-secondary:${branding.secondary_color}`,
    `--primary-foreground:${getContrastColor(branding.primary_color)}`,
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
