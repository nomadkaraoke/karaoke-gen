"use client"

import Image from "next/image"
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
  const logoHeight = branding.logo_height || 40

  // Size classes based on size prop - use static Tailwind classes only
  // Dynamic height is applied via inline style
  const sizeClasses =
    size === "lg"
      ? "max-w-[600px]"
      : "max-w-[200px]"

  // Compute max height for inline style
  const maxHeight = size === "lg" ? 120 : logoHeight

  // For tenant logos, use img tag since they come from GCS signed URLs
  // For default logo, use Next.js Image for optimization
  if (!isDefault && branding.logo_url) {
    return (
      <div className={`flex items-center ${className}`}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={logoUrl}
          alt={tenant?.name || "Logo"}
          className={`w-auto h-auto ${sizeClasses} object-contain`}
          style={{ maxHeight }}
        />
      </div>
    )
  }

  // Default Nomad Karaoke logo with Next.js Image optimization
  return (
    <div className={`flex items-center ${className}`}>
      <Image
        src={DEFAULT_LOGO_URL}
        alt="Nomad Karaoke"
        width={200}
        height={106}
        className={`w-auto h-auto ${sizeClasses} object-contain`}
        style={{ maxHeight }}
        priority
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
