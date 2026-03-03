"use client"

import { ImpersonationBanner } from "./impersonation-banner"
import { TenantPreviewBanner } from "./tenant-preview-banner"

/**
 * Client-side wrapper for the impersonation and tenant preview banners.
 * This is needed because the root layout is a server component,
 * but the banners need access to client-side auth/tenant state.
 */
export function ImpersonationBannerWrapper() {
  return (
    <>
      <ImpersonationBanner />
      <TenantPreviewBanner />
    </>
  )
}
