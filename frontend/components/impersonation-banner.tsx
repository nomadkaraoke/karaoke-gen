"use client"

import { useRouter } from "next/navigation"
import { useAuth } from "@/lib/auth"
import { Button } from "@/components/ui/button"
import { Eye, Shield } from "lucide-react"

/**
 * Banner shown when admin is impersonating another user.
 * Compact strip at the top of the page with clear identity and quick exit.
 */
export function ImpersonationBanner() {
  const { isImpersonating, impersonatedUserEmail, endImpersonation } = useAuth()
  const router = useRouter()

  if (!isImpersonating) {
    return null
  }

  const handleBackToAdmin = () => {
    endImpersonation()
    router.push("/admin")
  }

  return (
    <div className="border-b border-purple-500/30 bg-purple-950/60 px-4 py-1.5 flex items-center justify-between gap-4 text-xs">
      <div className="flex items-center gap-2 text-purple-300">
        <Eye className="w-3.5 h-3.5 shrink-0" />
        <span>
          Viewing as <strong className="text-purple-100">{impersonatedUserEmail}</strong>
        </span>
      </div>
      <Button
        variant="ghost"
        size="sm"
        onClick={handleBackToAdmin}
        className="h-6 px-2.5 text-xs gap-1.5 text-purple-200 hover:text-white hover:bg-purple-800/50"
      >
        <Shield className="w-3 h-3" />
        Back to Admin
      </Button>
    </div>
  )
}
