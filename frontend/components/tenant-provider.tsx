"use client"

import { useEffect, type ReactNode } from "react"
import { useTenant } from "@/lib/tenant"

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
  const { fetchTenantConfig, isInitialized } = useTenant()

  useEffect(() => {
    // Fetch tenant config on mount if not already initialized
    if (!isInitialized) {
      fetchTenantConfig()
    }
  }, [fetchTenantConfig, isInitialized])

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
