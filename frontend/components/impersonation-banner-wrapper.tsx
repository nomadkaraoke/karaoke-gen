"use client"

import { ImpersonationBanner } from "./impersonation-banner"

/**
 * Client-side wrapper for the impersonation banner.
 * This is needed because the root layout is a server component,
 * but the banner needs access to client-side auth state.
 */
export function ImpersonationBannerWrapper() {
  return <ImpersonationBanner />
}
