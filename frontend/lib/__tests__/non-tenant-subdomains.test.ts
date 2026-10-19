import { NON_TENANT_SUBDOMAINS } from "../edge/nonTenantSubdomains"

describe("NON_TENANT_SUBDOMAINS", () => {
  it("treats the voting-board vanity host as Nomad Karaoke, not a tenant", () => {
    // requests.nomadkaraoke.com serves the board directly; if it were detected as a
    // tenant the page would send X-Tenant-ID: requests and try to load tenant branding.
    expect(NON_TENANT_SUBDOMAINS).toContain("requests")
  })

  it("matches the backend list in backend/middleware/tenant.py", () => {
    expect([...NON_TENANT_SUBDOMAINS].sort()).toEqual(
      ["admin", "api", "app", "beta", "buy", "gen", "requests", "www"],
    )
  })
})
