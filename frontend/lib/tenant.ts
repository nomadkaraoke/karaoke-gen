"use client"

import { create } from "zustand"
import { API_BASE_URL } from "./api"

// Types for tenant configuration
export interface TenantBranding {
  logo_url: string | null
  logo_height: number
  primary_color: string
  secondary_color: string
  accent_color: string | null
  background_color: string | null
  favicon_url: string | null
  site_title: string
  tagline: string | null
}

export interface TenantFeatures {
  audio_search: boolean
  file_upload: boolean
  youtube_url: boolean
  youtube_upload: boolean
  dropbox_upload: boolean
  gdrive_upload: boolean
  theme_selection: boolean
  color_overrides: boolean
  enable_cdg: boolean
  enable_4k: boolean
  admin_access: boolean
}

export interface TenantDefaults {
  theme_id: string | null
  locked_theme: string | null
  distribution_mode: string
}

export interface TenantConfig {
  id: string
  name: string
  subdomain: string
  is_active: boolean
  branding: TenantBranding
  features: TenantFeatures
  defaults: TenantDefaults
  allowed_email_domains: string[]
}

export interface TenantConfigResponse {
  tenant: TenantConfig | null
  is_default: boolean
}

// Default Nomad Karaoke configuration (used when no tenant detected)
const DEFAULT_BRANDING: TenantBranding = {
  logo_url: null, // Will use default Nomad Karaoke logo
  logo_height: 40,
  primary_color: "#ff5bb8", // Pink
  secondary_color: "#8b5cf6", // Purple
  accent_color: "#ffdf6b", // Yellow
  background_color: null,
  favicon_url: null,
  site_title: "Nomad Karaoke Generator",
  tagline: null,
}

const DEFAULT_FEATURES: TenantFeatures = {
  audio_search: true,
  file_upload: true,
  youtube_url: true,
  youtube_upload: true,
  dropbox_upload: true,
  gdrive_upload: true,
  theme_selection: true,
  color_overrides: true,
  enable_cdg: true,
  enable_4k: true,
  admin_access: true,
}

const DEFAULT_DEFAULTS: TenantDefaults = {
  theme_id: null,
  locked_theme: null,
  distribution_mode: "all",
}

// Zustand store for tenant state
interface TenantStore {
  // State
  tenant: TenantConfig | null
  isDefault: boolean
  isLoading: boolean
  error: string | null
  isInitialized: boolean

  // Actions
  fetchTenantConfig: () => Promise<void>
  setTenant: (tenant: TenantConfig | null, isDefault: boolean) => void
  clearError: () => void
}

// Derived state helpers (computed outside store to avoid Zustand getter bug
// where Object.assign during set() converts getters to stale static values)
function getBranding(state: TenantStore): TenantBranding {
  return state.tenant?.branding ?? DEFAULT_BRANDING
}
function getFeatures(state: TenantStore): TenantFeatures {
  return state.tenant?.features ?? DEFAULT_FEATURES
}
function getDefaults(state: TenantStore): TenantDefaults {
  return state.tenant?.defaults ?? DEFAULT_DEFAULTS
}

/**
 * Detect tenant from current URL subdomain or admin preview param.
 * Returns tenant ID if on a tenant subdomain or previewing, null otherwise.
 */
function detectTenantFromUrl(): string | null {
  if (typeof window === "undefined") return null

  const hostname = window.location.hostname.toLowerCase()
  const params = new URLSearchParams(window.location.search)

  // Local development - check for tenant query param
  if (hostname === "localhost" || hostname === "127.0.0.1") {
    return params.get("tenant")
  }

  // Admin preview: ?preview_tenant=X on any domain
  // The preview banner and auth check happen in the UI layer
  const previewTenant = params.get("preview_tenant")
  if (previewTenant) {
    return previewTenant
  }

  // Production - check for subdomain
  // Strict patterns:
  // - {tenant}.nomadkaraoke.com (3 parts)
  // - {tenant}.gen.nomadkaraoke.com (4 parts with "gen" as second)
  if (hostname.includes("nomadkaraoke.com")) {
    const parts = hostname.split(".")
    // Skip known non-tenant subdomains
    const nonTenantSubdomains = ["gen", "api", "www", "buy", "admin", "app", "beta"]

    // Accept exactly 3 parts (tenant.nomadkaraoke.com)
    // or exactly 4 parts where second is "gen" (tenant.gen.nomadkaraoke.com)
    const isValidPattern =
      parts.length === 3 ||
      (parts.length === 4 && parts[1] === "gen")

    if (isValidPattern && !nonTenantSubdomains.includes(parts[0])) {
      return parts[0]
    }
  }

  return null
}

/**
 * Check if admin tenant preview is active (via ?preview_tenant=X query param).
 */
export function isPreviewingTenant(): boolean {
  if (typeof window === "undefined") return false
  const params = new URLSearchParams(window.location.search)
  return !!params.get("preview_tenant")
}

/**
 * Get the previewed tenant ID if admin preview is active.
 */
export function getPreviewTenantId(): string | null {
  if (typeof window === "undefined") return null
  const params = new URLSearchParams(window.location.search)
  return params.get("preview_tenant")
}

const useTenantStore = create<TenantStore>()((set, get) => ({
  // Initial state
  tenant: null,
  isDefault: true,
  isLoading: false,
  error: null,
  isInitialized: false,

  // Actions
  fetchTenantConfig: async () => {
    const state = get()
    if (state.isLoading) return // Already loading

    // Check for edge-injected config (from Cloudflare Pages Function)
    // This provides instant tenant config without a network request
    if (typeof window !== "undefined" && (window as any).__TENANT_CONFIG__) {
      const data = (window as any).__TENANT_CONFIG__ as TenantConfigResponse
      set({
        tenant: data.tenant,
        isDefault: data.is_default,
        isLoading: false,
        isInitialized: true,
      })
      if (data.tenant) {
        applyTenantBranding(data.tenant.branding)
      }
      return
    }

    set({ isLoading: true, error: null })

    try {
      // Detect tenant from URL
      const tenantId = detectTenantFromUrl()

      // Build URL with optional tenant query param
      let url = `${API_BASE_URL}/api/tenant/config`
      if (tenantId) {
        url += `?tenant=${encodeURIComponent(tenantId)}`
      }

      const response = await fetch(url, {
        method: "GET",
        headers: {
          "Content-Type": "application/json",
          // Include X-Tenant-ID header for backend detection
          ...(tenantId ? { "X-Tenant-ID": tenantId } : {}),
        },
      })

      if (!response.ok) {
        throw new Error("Failed to fetch tenant configuration")
      }

      const data: TenantConfigResponse = await response.json()

      set({
        tenant: data.tenant,
        isDefault: data.is_default,
        isLoading: false,
        isInitialized: true,
      })

      // Apply tenant branding to CSS variables
      if (data.tenant) {
        applyTenantBranding(data.tenant.branding)
      }
    } catch (err) {
      console.error("Failed to fetch tenant config:", err)
      set({
        tenant: null,
        isDefault: true,
        isLoading: false,
        error: err instanceof Error ? err.message : "Failed to load tenant configuration",
        isInitialized: true,
      })
    }
  },

  setTenant: (tenant, isDefault) => {
    set({ tenant, isDefault, isInitialized: true })
    if (tenant) {
      applyTenantBranding(tenant.branding)
    }
  },

  clearError: () => set({ error: null }),
}))

/**
 * Primary hook for accessing tenant state with derived values.
 * Consumers get branding/features/defaults computed fresh on each render.
 */
export function useTenant() {
  const store = useTenantStore()
  return {
    ...store,
    branding: getBranding(store),
    features: getFeatures(store),
    defaults: getDefaults(store),
    tenantId: store.tenant?.id ?? null,
  }
}

// Expose Zustand API surface for non-hook contexts and tests
useTenant.getState = () => {
  const state = useTenantStore.getState()
  return {
    ...state,
    branding: getBranding(state),
    features: getFeatures(state),
    defaults: getDefaults(state),
    tenantId: state.tenant?.id ?? null,
  }
}
useTenant.setState = useTenantStore.setState
useTenant.subscribe = useTenantStore.subscribe

/**
 * Apply tenant branding by setting CSS custom properties.
 * This allows dynamic theming without changing the stylesheet.
 */
function applyTenantBranding(branding: TenantBranding) {
  if (typeof document === "undefined") return

  const root = document.documentElement

  // Set CSS custom properties for tenant colors
  root.style.setProperty("--tenant-primary", branding.primary_color)
  root.style.setProperty("--tenant-secondary", branding.secondary_color)

  if (branding.accent_color) {
    root.style.setProperty("--tenant-accent", branding.accent_color)
  }

  if (branding.background_color) {
    root.style.setProperty("--tenant-background", branding.background_color)
  }

  // Update document title
  if (branding.site_title) {
    document.title = branding.site_title
  }

  // Update favicon if provided
  if (branding.favicon_url) {
    const link = document.querySelector<HTMLLinkElement>("link[rel~='icon']")
    if (link) {
      link.href = branding.favicon_url
    }
  }
}

/**
 * Hook to get tenant-aware API headers.
 * Use this when making API calls that need tenant context.
 */
export function getTenantHeaders(): Record<string, string> {
  const state = useTenant.getState()
  if (state.tenant?.id) {
    return { "X-Tenant-ID": state.tenant.id }
  }
  return {}
}

/**
 * Check if a feature is enabled for the current tenant.
 */
export function isFeatureEnabled(feature: keyof TenantFeatures): boolean {
  const state = useTenant.getState()
  const features = state.tenant?.features ?? DEFAULT_FEATURES
  return features[feature]
}

// NOTE: Tenant initialization is handled by TenantProvider component
// to avoid hydration race conditions and duplicate fetches.
// Do not auto-initialize here at module load time.
