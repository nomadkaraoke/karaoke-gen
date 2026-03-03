"use client"

import { useEffect, useState } from "react"
import { useTenant, isPreviewingTenant, getPreviewTenantId } from "@/lib/tenant"
import { useAuth } from "@/lib/auth"
import { Button } from "@/components/ui/button"
import { X, Eye } from "lucide-react"

/**
 * Banner shown when an admin is previewing a tenant portal via ?preview_tenant=X.
 * Only visible to admins. Shows which tenant is being previewed with an exit button.
 */
export function TenantPreviewBanner() {
  const { user } = useAuth()
  const { tenant } = useTenant()
  const [isPreviewing, setIsPreviewing] = useState(false)

  useEffect(() => {
    setIsPreviewing(isPreviewingTenant())
  }, [])

  // Only show for admins previewing a tenant
  if (!isPreviewing || user?.role !== "admin") {
    return null
  }

  const previewTenantId = getPreviewTenantId()
  const tenantName = tenant?.name || previewTenantId

  const handleExitPreview = () => {
    // Remove preview_tenant param and reload
    const url = new URL(window.location.href)
    url.searchParams.delete("preview_tenant")
    window.location.href = url.toString()
  }

  return (
    <div className="bg-purple-600 text-white px-4 py-2 flex items-center justify-between gap-4 text-sm font-medium">
      <div className="flex items-center gap-2">
        <Eye className="w-4 h-4" />
        <span>
          Previewing as <strong>{tenantName}</strong>
        </span>
      </div>
      <Button
        variant="outline"
        size="sm"
        onClick={handleExitPreview}
        className="bg-purple-100 hover:bg-purple-200 text-purple-950 border-purple-400 h-7 px-3"
      >
        <X className="w-3 h-3 mr-1" />
        Exit Preview
      </Button>
    </div>
  )
}
