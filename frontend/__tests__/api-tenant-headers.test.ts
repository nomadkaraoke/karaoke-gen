/**
 * @jest-environment jsdom
 *
 * Tests that the API client attaches the X-Tenant-ID header on white-label tenant
 * portals (sourced from window.__TENANT_CONFIG__) and omits it on the consumer build.
 *
 * Regression guard for the root-cause bug: the frontend never sent X-Tenant-ID, so the
 * shared api.nomadkaraoke.com backend could not detect the tenant and every tenant-portal
 * job/sign-in silently fell back to consumer behavior (0 tenant jobs ever ran in prod).
 */

import { api, getTenantId } from '@/lib/api'

function setEdgeTenant(id: string | null) {
  if (id) {
    ;(window as any).__TENANT_CONFIG__ = {
      tenant: { id, name: id, subdomain: `${id}.nomadkaraoke.com` },
      is_default: false,
    }
  } else {
    delete (window as any).__TENANT_CONFIG__
  }
}

describe('getTenantId', () => {
  afterEach(() => setEdgeTenant(null))

  it('returns null when no tenant config is present (consumer build)', () => {
    setEdgeTenant(null)
    expect(getTenantId()).toBeNull()
  })

  it('returns the tenant id from window.__TENANT_CONFIG__', () => {
    setEdgeTenant('vocalstar')
    expect(getTenantId()).toBe('vocalstar')
  })

  it('returns null when the injected config has no tenant (default/consumer domain)', () => {
    ;(window as any).__TENANT_CONFIG__ = { tenant: null, is_default: true }
    expect(getTenantId()).toBeNull()
  })
})

describe('X-Tenant-ID header attachment', () => {
  const fetchMock = jest.fn()

  beforeEach(() => {
    fetchMock.mockReset()
    ;(global as any).fetch = fetchMock
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({}) })
  })

  afterEach(() => setEdgeTenant(null))

  it('attaches X-Tenant-ID to authenticated requests (createJobWithUploadUrls) on a tenant portal', async () => {
    setEdgeTenant('vocalstar')
    window.localStorage.setItem('karaoke_access_token', 'tok-123')

    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ job_id: 'j1', upload_urls: [] }),
    })

    await api.createJobWithUploadUrls('Adele', 'Hello', [], {
      is_private: true,
      existing_instrumental: true,
    })

    const [, init] = fetchMock.mock.calls[0]
    expect(init.headers['X-Tenant-ID']).toBe('vocalstar')
    expect(init.headers['Authorization']).toBe('Bearer tok-123')

    window.localStorage.removeItem('karaoke_access_token')
  })

  it('attaches X-Tenant-ID to the magic-link request on a tenant portal', async () => {
    setEdgeTenant('singa')
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ success: true }) })

    await api.sendMagicLink('user@singa.com')

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toMatch(/\/api\/users\/auth\/magic-link$/)
    expect(init.headers['X-Tenant-ID']).toBe('singa')
  })

  it('does NOT attach X-Tenant-ID on the consumer build', async () => {
    setEdgeTenant(null)
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ success: true }) })

    await api.sendMagicLink('user@example.com')

    const [, init] = fetchMock.mock.calls[0]
    expect(init.headers['X-Tenant-ID']).toBeUndefined()
  })
})
