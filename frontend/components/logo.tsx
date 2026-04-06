import { TenantLogo } from "./tenant-logo"

export function Logo({ className = "", size = "sm" }: { className?: string; size?: "sm" | "lg" }) {
  return <TenantLogo className={className} size={size} />
}
