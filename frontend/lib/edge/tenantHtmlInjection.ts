/**
 * HTML rewriting for tenant portal pages, done by the Cloudflare Pages Function.
 *
 * The static export is prerendered with the default (Nomad Karaoke) branding,
 * so without help a tenant portal paints the Nomad logo / consumer landing page
 * until React hydrates and swaps in the tenant's branding (~100ms+, longer on a
 * cold load). The edge already knows the tenant, so it:
 * - injects the config (window.__TENANT_CONFIG__) + tenant CSS variables,
 * - marks <html data-tenant-pending> and hides <body> until the app has applied
 *   the tenant branding (TenantProvider removes the marker; a timeout reveals the
 *   page anyway if the JS never runs),
 * - preloads the tenant logo so it is ready when the page is revealed,
 * - swaps the <title> for the tenant's site title.
 */

import type { TenantConfigResponse } from "./tenantConfigCache"

export const TENANT_PENDING_ATTR = "data-tenant-pending"
export const REVEAL_TIMEOUT_MS = 4000

const API_ASSET_BASE = "https://api.nomadkaraoke.com/api/tenant/asset"

/** Compute a contrasting foreground color (black or white) for a hex color (WCAG luminance). */
export function getContrastColor(hexColor: string): string {
  const hex = hexColor.replace("#", "")
  const r = parseInt(hex.slice(0, 2), 16) / 255
  const g = parseInt(hex.slice(2, 4), 16) / 255
  const b = parseInt(hex.slice(4, 6), 16) / 255

  const toLinear = (c: number) => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4))
  const luminance = 0.2126 * toLinear(r) + 0.7152 * toLinear(g) + 0.0722 * toLinear(b)

  return luminance > 0.179 ? "#000000" : "#ffffff"
}

/** Escape a string for safe injection into a <script> tag (prevents </script> / <!-- breakouts). */
export function escapeForScript(json: string): string {
  return json.replace(/</g, "\\u003c").replace(/>/g, "\\u003e").replace(/&/g, "\\u0026")
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
}

/** The logo URL the browser will load (mirrors TenantLogo's gs:// → asset endpoint mapping). */
export function resolveLogoUrl(tenantId: string, logoUrl: string | null | undefined): string | null {
  if (!logoUrl) return null
  if (logoUrl.startsWith("gs://")) {
    const filename = logoUrl.split("/").pop()
    return filename ? `${API_ASSET_BASE}/${tenantId}/${filename}` : null
  }
  return /^https?:\/\//.test(logoUrl) ? logoUrl : null
}

/** Rewrite a prerendered HTML page for the given tenant config. Returns the HTML unchanged if there is no tenant. */
export function injectTenantIntoHtml(html: string, config: TenantConfigResponse): string {
  const tenant = config.tenant
  if (!tenant) return html
  const branding = tenant.branding

  const cssVars = [
    `--tenant-primary:${branding.primary_color}`,
    `--tenant-secondary:${branding.secondary_color}`,
    `--primary-foreground:${getContrastColor(branding.primary_color)}`,
  ]
  if (branding.accent_color) cssVars.push(`--tenant-accent:${branding.accent_color}`)
  if (branding.background_color) cssVars.push(`--tenant-background:${branding.background_color}`)

  const logoUrl = resolveLogoUrl(tenant.id, branding.logo_url)

  const injection = [
    `<script>window.__TENANT_CONFIG__=${escapeForScript(JSON.stringify(config))};` +
      `(function(d){d.setAttribute("${TENANT_PENDING_ATTR}","");` +
      `setTimeout(function(){d.removeAttribute("${TENANT_PENDING_ATTR}")},${REVEAL_TIMEOUT_MS})})(document.documentElement);</script>`,
    `<style>:root{${cssVars.join(";")}}html[${TENANT_PENDING_ATTR}] body{visibility:hidden}</style>`,
    ...(logoUrl ? [`<link rel="preload" as="image" href="${escapeHtml(logoUrl)}">`] : []),
  ].join("\n")

  let out = html.replace("</head>", injection + "\n</head>")
  if (branding.site_title) {
    out = out.replace(/<title>[^<]*<\/title>/, `<title>${escapeHtml(branding.site_title)}</title>`)
  }
  return out
}
