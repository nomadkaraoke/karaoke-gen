/**
 * Tests for tenant store and utilities
 *
 * These tests verify the tenant configuration system for white-label portals.
 * They test the Zustand store, helper functions, and URL detection logic.
 */

import {
  useTenant,
  getTenantHeaders,
  isFeatureEnabled,
  TenantConfig,
} from "../tenant"

// Mock fetch globally
global.fetch = jest.fn()

// Mock document for applyTenantBranding tests
const mockSetProperty = jest.fn()
Object.defineProperty(document, "documentElement", {
  value: {
    style: {
      setProperty: mockSetProperty,
    },
  },
  writable: true,
})

// Sample tenant config for testing
const SAMPLE_VOCALSTAR_CONFIG: TenantConfig = {
  id: "vocalstar",
  name: "Vocal Star",
  subdomain: "vocalstar.nomadkaraoke.com",
  is_active: true,
  branding: {
    logo_url: "https://example.com/logo.png",
    logo_height: 50,
    primary_color: "#ffff00",
    secondary_color: "#006CF9",
    accent_color: "#ff0000",
    background_color: "#000000",
    favicon_url: "https://example.com/favicon.ico",
    site_title: "Vocal Star Karaoke Generator",
    tagline: "Professional Karaoke",
  },
  features: {
    audio_search: false,
    file_upload: true,
    youtube_url: false,
    youtube_upload: false,
    dropbox_upload: false,
    gdrive_upload: false,
    theme_selection: false,
    color_overrides: false,
    enable_cdg: true,
    enable_4k: true,
    admin_access: false,
  },
  defaults: {
    theme_id: "vocalstar",
    locked_theme: "vocalstar",
    distribution_mode: "download_only",
  },
  allowed_email_domains: ["vocal-star.com", "vocalstarmusic.com"],
}

// Helper to reset store state
function resetStore() {
  useTenant.setState({
    tenant: null,
    isDefault: true,
    isLoading: false,
    error: null,
    isInitialized: false,
  })
}

describe("useTenant store", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    resetStore()
    mockSetProperty.mockClear()
  })

  describe("initial state", () => {
    it("should have null tenant initially", () => {
      const state = useTenant.getState()
      expect(state.tenant).toBeNull()
      expect(state.isDefault).toBe(true)
      expect(state.isLoading).toBe(false)
      expect(state.error).toBeNull()
      expect(state.isInitialized).toBe(false)
    })
  })

  describe("computed values", () => {
    it("should return default branding when no tenant", () => {
      const state = useTenant.getState()
      expect(state.branding.primary_color).toBe("#ff5bb8")
      expect(state.branding.secondary_color).toBe("#8b5cf6")
      expect(state.branding.site_title).toBe("Nomad Karaoke Generator")
    })

    it("should return tenant branding when tenant is set via setTenant", () => {
      // Use setTenant action (not direct setState) to properly update state
      useTenant.getState().setTenant(SAMPLE_VOCALSTAR_CONFIG, false)
      const state = useTenant.getState()
      expect(state.tenant?.branding.primary_color).toBe("#ffff00")
      expect(state.tenant?.branding.site_title).toBe("Vocal Star Karaoke Generator")
    })

    it("should return tenant branding via computed property after setTenant (regression: Zustand getter bug)", () => {
      // This tests the computed `branding` property, not `tenant.branding`.
      // The Zustand getter bug caused Object.assign during set() to convert
      // getter properties to stale static values, so `state.branding` would
      // always return DEFAULT_BRANDING even after tenant was loaded.
      useTenant.getState().setTenant(SAMPLE_VOCALSTAR_CONFIG, false)
      const state = useTenant.getState()
      // These use the derived `branding` property, not `tenant?.branding`
      expect(state.branding.primary_color).toBe("#ffff00")
      expect(state.branding.secondary_color).toBe("#006CF9")
      expect(state.branding.site_title).toBe("Vocal Star Karaoke Generator")
      expect(state.branding.tagline).toBe("Professional Karaoke")
    })

    it("should return default features when no tenant", () => {
      const state = useTenant.getState()
      expect(state.features.audio_search).toBe(true)
      expect(state.features.file_upload).toBe(true)
      expect(state.features.youtube_upload).toBe(true)
    })

    it("should return tenant features when tenant is set via setTenant", () => {
      useTenant.getState().setTenant(SAMPLE_VOCALSTAR_CONFIG, false)
      const state = useTenant.getState()
      expect(state.tenant?.features.audio_search).toBe(false)
      expect(state.tenant?.features.youtube_upload).toBe(false)
      expect(state.tenant?.features.file_upload).toBe(true)
    })

    it("should return tenant features via computed property after setTenant (regression: Zustand getter bug)", () => {
      useTenant.getState().setTenant(SAMPLE_VOCALSTAR_CONFIG, false)
      const state = useTenant.getState()
      // These use the derived `features` property, not `tenant?.features`
      expect(state.features.audio_search).toBe(false)
      expect(state.features.youtube_upload).toBe(false)
      expect(state.features.file_upload).toBe(true)
      expect(state.features.enable_cdg).toBe(true)
    })

    it("should return tenantId when tenant is set via setTenant", () => {
      useTenant.getState().setTenant(SAMPLE_VOCALSTAR_CONFIG, false)
      const state = useTenant.getState()
      expect(state.tenant?.id).toBe("vocalstar")
    })

    it("should return tenantId via computed property after setTenant (regression: Zustand getter bug)", () => {
      useTenant.getState().setTenant(SAMPLE_VOCALSTAR_CONFIG, false)
      const state = useTenant.getState()
      // Uses the derived `tenantId` property, not `tenant?.id`
      expect(state.tenantId).toBe("vocalstar")
    })

    it("should return correct defaults via computed property after setTenant", () => {
      useTenant.getState().setTenant(SAMPLE_VOCALSTAR_CONFIG, false)
      const state = useTenant.getState()
      expect(state.defaults.theme_id).toBe("vocalstar")
      expect(state.defaults.locked_theme).toBe("vocalstar")
      expect(state.defaults.distribution_mode).toBe("download_only")
    })

    it("should return fresh computed values after multiple set() calls (regression: Zustand getter bug)", () => {
      // The original bug: each set() call would freeze computed values.
      // After set({isLoading: true}), branding would permanently return defaults.
      useTenant.setState({ isLoading: true })
      useTenant.setState({ isLoading: false })
      useTenant.getState().setTenant(SAMPLE_VOCALSTAR_CONFIG, false)

      const state = useTenant.getState()
      // After multiple set() calls, computed values must still reflect tenant data
      expect(state.branding.primary_color).toBe("#ffff00")
      expect(state.features.audio_search).toBe(false)
      expect(state.defaults.theme_id).toBe("vocalstar")
      expect(state.tenantId).toBe("vocalstar")
    })

    it("should return null tenantId when no tenant", () => {
      const state = useTenant.getState()
      expect(state.tenantId).toBeNull()
    })
  })

  describe("fetchTenantConfig", () => {
    it("should fetch and set tenant config", async () => {
      ;(global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          tenant: SAMPLE_VOCALSTAR_CONFIG,
          is_default: false,
        }),
      })

      await useTenant.getState().fetchTenantConfig()

      const state = useTenant.getState()
      expect(state.tenant?.id).toBe("vocalstar")
      expect(state.isDefault).toBe(false)
      expect(state.isInitialized).toBe(true)
    })

    it("should apply branding CSS variables on fetch", async () => {
      ;(global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          tenant: SAMPLE_VOCALSTAR_CONFIG,
          is_default: false,
        }),
      })

      await useTenant.getState().fetchTenantConfig()

      expect(mockSetProperty).toHaveBeenCalledWith("--tenant-primary", "#ffff00")
      expect(mockSetProperty).toHaveBeenCalledWith("--tenant-secondary", "#006CF9")
    })

    it("should handle fetch error gracefully", async () => {
      ;(global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: false,
        status: 500,
      })

      await useTenant.getState().fetchTenantConfig()

      const state = useTenant.getState()
      expect(state.tenant).toBeNull()
      expect(state.isDefault).toBe(true)
      expect(state.error).toBeTruthy()
      expect(state.isInitialized).toBe(true)
    })

    it("should handle network error gracefully", async () => {
      ;(global.fetch as jest.Mock).mockRejectedValueOnce(new Error("Network error"))

      await useTenant.getState().fetchTenantConfig()

      const state = useTenant.getState()
      expect(state.tenant).toBeNull()
      expect(state.error).toBe("Network error")
      expect(state.isInitialized).toBe(true)
    })

    it("should not fetch if already loading", async () => {
      useTenant.setState({ isLoading: true })

      await useTenant.getState().fetchTenantConfig()

      expect(global.fetch).not.toHaveBeenCalled()
    })

    it("should set default config when no tenant returned", async () => {
      ;(global.fetch as jest.Mock).mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          tenant: null,
          is_default: true,
        }),
      })

      await useTenant.getState().fetchTenantConfig()

      const state = useTenant.getState()
      expect(state.tenant).toBeNull()
      expect(state.isDefault).toBe(true)
      expect(state.isInitialized).toBe(true)
    })
  })

  describe("setTenant", () => {
    it("should set tenant directly", () => {
      useTenant.getState().setTenant(SAMPLE_VOCALSTAR_CONFIG, false)

      const state = useTenant.getState()
      expect(state.tenant?.id).toBe("vocalstar")
      expect(state.isDefault).toBe(false)
      expect(state.isInitialized).toBe(true)
    })

    it("should apply branding when setting tenant", () => {
      useTenant.getState().setTenant(SAMPLE_VOCALSTAR_CONFIG, false)

      expect(mockSetProperty).toHaveBeenCalledWith("--tenant-primary", "#ffff00")
    })

    it("should handle null tenant", () => {
      useTenant.setState({ tenant: SAMPLE_VOCALSTAR_CONFIG })
      useTenant.getState().setTenant(null, true)

      const state = useTenant.getState()
      expect(state.tenant).toBeNull()
      expect(state.isDefault).toBe(true)
    })
  })

  describe("clearError", () => {
    it("should clear error", () => {
      useTenant.setState({ error: "Some error" })

      expect(useTenant.getState().error).toBe("Some error")

      useTenant.getState().clearError()

      expect(useTenant.getState().error).toBeNull()
    })
  })
})

describe("getTenantHeaders", () => {
  beforeEach(() => {
    resetStore()
  })

  it("should return empty object when no tenant", () => {
    const headers = getTenantHeaders()
    expect(headers).toEqual({})
  })

  it("should return X-Tenant-ID header when tenant is set via setTenant", () => {
    useTenant.getState().setTenant(SAMPLE_VOCALSTAR_CONFIG, false)

    const headers = getTenantHeaders()
    expect(headers).toEqual({ "X-Tenant-ID": "vocalstar" })
  })
})

describe("isFeatureEnabled", () => {
  beforeEach(() => {
    resetStore()
  })

  it("should return default feature values when no tenant", () => {
    expect(isFeatureEnabled("audio_search")).toBe(true)
    expect(isFeatureEnabled("file_upload")).toBe(true)
    expect(isFeatureEnabled("youtube_upload")).toBe(true)
    // admin_access defaults to true on frontend (for Nomad Karaoke default experience)
    expect(isFeatureEnabled("admin_access")).toBe(true)
  })

  it("should return tenant feature values when tenant is set via setTenant", () => {
    useTenant.getState().setTenant(SAMPLE_VOCALSTAR_CONFIG, false)

    expect(isFeatureEnabled("audio_search")).toBe(false)
    expect(isFeatureEnabled("youtube_upload")).toBe(false)
    expect(isFeatureEnabled("file_upload")).toBe(true)
    expect(isFeatureEnabled("enable_cdg")).toBe(true)
  })
})

describe("API fetch behavior", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    resetStore()
  })

  it("should call API endpoint when fetching tenant config", async () => {
    ;(global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ tenant: null, is_default: true }),
    })

    await useTenant.getState().fetchTenantConfig()

    expect(global.fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/tenant/config"),
      expect.any(Object)
    )
  })
})
