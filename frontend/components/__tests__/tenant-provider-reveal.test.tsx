import { render, waitFor } from "@testing-library/react"
import { TenantProvider } from "../tenant-provider"
import { useTenant } from "@/lib/tenant"
import { TENANT_PENDING_ATTR } from "@/lib/edge/tenantHtmlInjection"

const VOCALSTAR = {
  tenant: {
    id: "vocalstar",
    name: "Vocal Star",
    subdomain: "vocalstar.nomadkaraoke.com",
    is_active: true,
    branding: {
      logo_url: "https://api.nomadkaraoke.com/api/tenant/asset/vocalstar/logo.jpg",
      logo_height: 60,
      primary_color: "#ffff00",
      secondary_color: "#006CF9",
      accent_color: null,
      background_color: null,
      favicon_url: null,
      site_title: "Vocal Star Karaoke Generator",
      tagline: null,
    },
    features: {},
    defaults: {},
    allowed_email_domains: [],
  },
  is_default: false,
}

function Probe() {
  const { isDefault } = useTenant()
  // Records what was rendered at the moment the page was revealed
  return <span data-testid="brand">{isDefault ? "nomad" : "tenant"}</span>
}

describe("TenantProvider reveal", () => {
  afterEach(() => {
    delete (window as unknown as Record<string, unknown>).__TENANT_CONFIG__
    document.documentElement.removeAttribute(TENANT_PENDING_ATTR)
    useTenant.setState({ tenant: null, isDefault: true, isInitialized: false, isLoading: false, error: null })
  })

  it("reveals the edge-hidden page only once tenant branding has rendered", async () => {
    ;(window as unknown as Record<string, unknown>).__TENANT_CONFIG__ = VOCALSTAR
    document.documentElement.setAttribute(TENANT_PENDING_ATTR, "")

    const brandAtReveal: string[] = []
    const observer = new MutationObserver(() => {
      if (!document.documentElement.hasAttribute(TENANT_PENDING_ATTR)) {
        brandAtReveal.push(document.querySelector("[data-testid=brand]")?.textContent ?? "")
      }
    })
    observer.observe(document.documentElement, { attributes: true })

    render(
      <TenantProvider>
        <Probe />
      </TenantProvider>
    )

    await waitFor(() => expect(document.documentElement.hasAttribute(TENANT_PENDING_ATTR)).toBe(false))
    observer.disconnect()
    expect(brandAtReveal).toEqual(["tenant"])
  })

  it("reveals the page even when the tenant config fetch fails", async () => {
    document.documentElement.setAttribute(TENANT_PENDING_ATTR, "")
    const fetchMock = jest.fn().mockRejectedValue(new Error("network down"))
    const original = global.fetch
    global.fetch = fetchMock as unknown as typeof fetch
    const errSpy = jest.spyOn(console, "error").mockImplementation(() => {})
    try {
      render(
        <TenantProvider>
          <Probe />
        </TenantProvider>
      )
      await waitFor(() => expect(document.documentElement.hasAttribute(TENANT_PENDING_ATTR)).toBe(false))
    } finally {
      global.fetch = original
      errSpy.mockRestore()
    }
  })
})
