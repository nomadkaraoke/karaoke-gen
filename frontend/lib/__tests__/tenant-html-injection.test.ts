import {
  injectTenantIntoHtml,
  resolveLogoUrl,
  TENANT_PENDING_ATTR,
  REVEAL_TIMEOUT_MS,
} from "../edge/tenantHtmlInjection"
import type { TenantConfigResponse } from "../edge/tenantConfigCache"

const PAGE =
  '<!DOCTYPE html><html><head><meta charset="utf-8"/><title>Nomad Karaoke: Generator</title></head>' +
  '<body><img src="/nomad-karaoke-logo.svg" alt="Nomad Karaoke"/></body></html>'

function config(branding: Partial<NonNullable<TenantConfigResponse["tenant"]>["branding"]> = {}): TenantConfigResponse {
  return {
    is_default: false,
    tenant: {
      id: "vocalstar",
      name: "Vocal Star",
      subdomain: "vocalstar.nomadkaraoke.com",
      is_active: true,
      branding: {
        logo_url: "https://api.nomadkaraoke.com/api/tenant/asset/vocalstar/logo.jpg",
        primary_color: "#ffff00",
        secondary_color: "#006CF9",
        accent_color: null,
        background_color: null,
        favicon_url: null,
        site_title: "Vocal Star Karaoke Generator",
        ...branding,
      },
    },
  }
}

function headOf(html: string): string {
  return html.slice(0, html.indexOf("</head>"))
}

describe("injectTenantIntoHtml", () => {
  it("hides the prerendered default branding until the app reveals it", () => {
    const head = headOf(injectTenantIntoHtml(PAGE, config()))
    expect(head).toContain(`html[${TENANT_PENDING_ATTR}] body{visibility:hidden}`)
    expect(head).toContain(`d.setAttribute("${TENANT_PENDING_ATTR}","")`)
  })

  it("always reveals the page after a timeout, even if the app never runs", () => {
    const head = headOf(injectTenantIntoHtml(PAGE, config()))
    expect(head).toContain(`removeAttribute("${TENANT_PENDING_ATTR}")},${REVEAL_TIMEOUT_MS}`)
  })

  it("marks the page pending synchronously in a real document and unmarks it after the timeout", () => {
    jest.useFakeTimers()
    try {
      const html = injectTenantIntoHtml(PAGE, config())
      const script = html.match(/<script>([\s\S]*?)<\/script>/)![1]
      document.documentElement.removeAttribute(TENANT_PENDING_ATTR)
      new Function(script)()
      expect(document.documentElement.hasAttribute(TENANT_PENDING_ATTR)).toBe(true)
      expect((window as unknown as { __TENANT_CONFIG__: TenantConfigResponse }).__TENANT_CONFIG__.tenant?.id).toBe("vocalstar")
      jest.advanceTimersByTime(REVEAL_TIMEOUT_MS)
      expect(document.documentElement.hasAttribute(TENANT_PENDING_ATTR)).toBe(false)
    } finally {
      jest.useRealTimers()
    }
  })

  it("preloads the tenant logo", () => {
    expect(headOf(injectTenantIntoHtml(PAGE, config()))).toContain(
      '<link rel="preload" as="image" href="https://api.nomadkaraoke.com/api/tenant/asset/vocalstar/logo.jpg">'
    )
  })

  it("skips the preload when the tenant has no logo", () => {
    expect(injectTenantIntoHtml(PAGE, config({ logo_url: null }))).not.toContain('rel="preload"')
  })

  it("replaces the title with the tenant's site title, escaped", () => {
    const html = injectTenantIntoHtml(PAGE, config({ site_title: "Vocal <Star> & Co" }))
    expect(html).toContain("<title>Vocal &lt;Star&gt; &amp; Co</title>")
    expect(html).not.toContain("Nomad Karaoke: Generator")
  })

  it("injects tenant colours and the escaped config", () => {
    const html = injectTenantIntoHtml(PAGE, config({ site_title: "</script><b>x" }))
    expect(html).toContain("--tenant-primary:#ffff00")
    expect(html).toContain("--primary-foreground:#000000")
    expect(html).not.toContain("</script><b>")
  })

  it("leaves non-tenant pages untouched", () => {
    expect(injectTenantIntoHtml(PAGE, { tenant: null, is_default: true })).toBe(PAGE)
  })
})

describe("resolveLogoUrl", () => {
  it("maps gs:// logos to the tenant asset endpoint", () => {
    expect(resolveLogoUrl("vocalstar", "gs://bucket/tenants/vocalstar/logo.png")).toBe(
      "https://api.nomadkaraoke.com/api/tenant/asset/vocalstar/logo.png"
    )
  })

  it("passes http(s) URLs through and drops anything else", () => {
    expect(resolveLogoUrl("t", "https://x.test/l.png")).toBe("https://x.test/l.png")
    expect(resolveLogoUrl("t", "javascript:alert(1)")).toBeNull()
    expect(resolveLogoUrl("t", null)).toBeNull()
  })
})
