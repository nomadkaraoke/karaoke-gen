"use client"

import { useTenant } from "@/lib/tenant"

// Default Nomad Karaoke logo URL (local SVG in /public)
const DEFAULT_LOGO_URL = "/nomad-karaoke-logo.svg"

interface TenantLogoProps {
  className?: string
  size?: "sm" | "lg"
}

/**
 * Logo component that displays either the tenant's logo or the default Nomad Karaoke logo.
 * Automatically adapts based on the current tenant configuration.
 */
export function TenantLogo({ className = "", size = "sm" }: TenantLogoProps) {
  const { tenant, branding, isDefault } = useTenant()

  // Determine logo URL and dimensions
  // Convert gs:// URLs to the API asset endpoint
  let logoUrl = branding.logo_url || DEFAULT_LOGO_URL
  if (logoUrl.startsWith("gs://") && tenant?.id) {
    const filename = logoUrl.split("/").pop()
    if (filename) {
      logoUrl = `https://api.nomadkaraoke.com/api/tenant/asset/${tenant.id}/${filename}`
    } else {
      logoUrl = DEFAULT_LOGO_URL
    }
  }

  const heightClass = size === "lg" ? "h-20 sm:h-[120px]" : "h-8 sm:h-10"
  const maxWidthClass = size === "lg" ? "max-w-[600px]" : "max-w-[200px]"

  return (
    <div className={`flex items-center ${className}`}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={logoUrl}
        alt={(!isDefault && tenant?.name) ? tenant.name : "Nomad Karaoke"}
        className={`${heightClass} ${maxWidthClass} w-auto object-contain shrink-0`}
      />
    </div>
  )
}

/**
 * Standalone Logo component for backward compatibility.
 * Simply wraps TenantLogo.
 */
export function Logo({ className = "", size = "sm" }: TenantLogoProps) {
  return <TenantLogo className={className} size={size} />
}
