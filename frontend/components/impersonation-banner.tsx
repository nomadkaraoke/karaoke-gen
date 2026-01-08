"use client"

import { useAuth } from "@/lib/auth"
import { Button } from "@/components/ui/button"
import { X, Eye } from "lucide-react"

/**
 * Banner shown when admin is impersonating another user.
 * Displays prominently at the top of the page with option to stop impersonating.
 */
export function ImpersonationBanner() {
  const { isImpersonating, impersonatedUserEmail, endImpersonation } = useAuth()

  if (!isImpersonating) {
    return null
  }

  return (
    <div className="bg-amber-500 text-amber-950 px-4 py-2 flex items-center justify-between gap-4 text-sm font-medium">
      <div className="flex items-center gap-2">
        <Eye className="w-4 h-4" />
        <span>
          Viewing as <strong>{impersonatedUserEmail}</strong>
        </span>
      </div>
      <Button
        variant="outline"
        size="sm"
        onClick={endImpersonation}
        className="bg-amber-100 hover:bg-amber-200 text-amber-950 border-amber-600 h-7 px-3"
      >
        <X className="w-3 h-3 mr-1" />
        Stop Impersonating
      </Button>
    </div>
  )
}
