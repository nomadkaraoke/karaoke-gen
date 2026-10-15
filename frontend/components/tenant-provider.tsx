"use client"

import { useEffect, type ReactNode } from "react"
import { useTenant } from "@/lib/tenant"
import { TENANT_PENDING_ATTR } from "@/lib/edge/tenantHtmlInjection"

interface TenantProviderProps {
  children: ReactNode
}

/**
 * Provider component that initializes tenant configuration on mount.
 * Wrap your app with this component to enable multi-tenant support.
 *
 * The provider:
 * 1. Detects tenant from URL subdomain or query param
 * 2. Fetches tenant configuration from API
 * 3. Applies tenant branding (colors, logo, title)
 *
 * Usage:
 * ```tsx
 * <TenantProvider>
 *   <YourApp />
 * </TenantProvider>
 * ```
 */
export function TenantProvider({ children }: TenantProviderProps) {
  const { fetchTenantConfig, isInitialized, isDefault, branding } = useTenant()
  const tenantTitle = !isDefault ? branding.site_title : null

  useEffect(() => {
    // Fetch tenant config on mount if not already initialized
    if (!isInitialized) {
      fetchTenantConfig()
    }
  }, [fetchTenantConfig, isInitialized])

  // On tenant portals the edge hides the prerendered (default-branded) page until
  // the tenant branding is applied. Parent effects run after the children's commit,
  // so by now the tenant logo/landing page has replaced the default markup.
  useEffect(() => {
    if (isInitialized) {
      document.documentElement.removeAttribute(TENANT_PENDING_ATTR)
    }
  }, [isInitialized])

  // Next.js metadata rewrites <title> ("Nomad Karaoke: Generator") on hydration and
  // on every route change, so keep the tenant's site title pinned.
  useEffect(() => {
    if (!tenantTitle) return
    const pin = () => {
      if (document.title !== tenantTitle) document.title = tenantTitle
    }
    pin()
    const observer = new MutationObserver(pin)
    observer.observe(document.head, { subtree: true, childList: true, characterData: true })
    return () => observer.disconnect()
  }, [tenantTitle])

  return <>{children}</>
}

/**
 * Hook to check if tenant is ready (initialized and not loading).
 * Use this to conditionally render content that depends on tenant config.
 */
export function useTenantReady(): boolean {
  const { isInitialized, isLoading } = useTenant()
  return isInitialized && !isLoading
}

/**
 * Component that only renders children when tenant is ready.
 * Shows a loading state while tenant config is being fetched.
 */
export function TenantReady({
  children,
  fallback,
}: {
  children: ReactNode
  fallback?: ReactNode
}) {
  const isReady = useTenantReady()

  if (!isReady) {
    return fallback ?? null
  }

  return <>{children}</>
}
