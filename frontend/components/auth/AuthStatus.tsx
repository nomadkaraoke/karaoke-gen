"use client"

import { useState, useEffect } from "react"
import { User, LogOut, CreditCard, Coins, KeyRound, Shield } from "lucide-react"
import Link from "next/link"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useAuth } from "@/lib/auth"
import { AuthDialog } from "./AuthDialog"

interface AuthStatusProps {
  onAuthChange?: () => void
}

export function AuthStatus({ onAuthChange }: AuthStatusProps) {
  const { user, logout } = useAuth()
  const [showAuthDialog, setShowAuthDialog] = useState(false)
  const [mounted, setMounted] = useState(false)

  // Avoid hydration mismatch
  useEffect(() => {
    setMounted(true)
  }, [])

  const handleLogout = async () => {
    await logout()
    onAuthChange?.()
    window.location.reload()
  }

  const handleAuthSuccess = () => {
    setShowAuthDialog(false)
    onAuthChange?.()
    window.location.reload()
  }

  const handleBuyCredits = () => {
    // Navigate to landing page pricing section with email prefilled if available
    const buyUrl = new URL('/#pricing', window.location.origin)
    if (user?.email) {
      buyUrl.searchParams.set("email", user.email)
    }
    window.location.href = buyUrl.toString()
  }

  // Don't render until mounted (avoids hydration issues)
  if (!mounted) {
    return null
  }

  if (user) {
    return (
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="sm"
            className="text-slate-300 hover:text-white flex items-center gap-2 min-h-[40px] px-2 sm:px-3"
          >
            <User className="w-4 h-4" />
            <span className="hidden sm:inline max-w-[150px] truncate">
              {user.display_name || user.email}
            </span>
            <span className="flex items-center gap-1 text-amber-400 font-medium">
              <Coins className="w-3 h-3" />
              {user.credits}
            </span>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-56 bg-slate-900 border-slate-700">
          <DropdownMenuLabel className="text-slate-400 font-normal">
            <div className="flex flex-col space-y-1">
              <p className="text-sm font-medium text-white">
                {user.display_name || "Karaoke Fan"}
              </p>
              <p className="text-xs text-slate-500 truncate">{user.email}</p>
            </div>
          </DropdownMenuLabel>
          <DropdownMenuSeparator className="bg-slate-700" />
          <DropdownMenuItem
            className="text-slate-300 focus:text-white focus:bg-slate-800 cursor-default"
            disabled
          >
            <Coins className="w-4 h-4 mr-2 text-amber-400" />
            <span>{user.credits} credits available</span>
          </DropdownMenuItem>
          <DropdownMenuItem
            onClick={handleBuyCredits}
            className="text-slate-300 focus:text-white focus:bg-slate-800"
          >
            <CreditCard className="w-4 h-4 mr-2" />
            <span>Buy More Credits</span>
          </DropdownMenuItem>
          {(user.role === "admin" || user.email?.endsWith("@nomadkaraoke.com")) && (
            <>
              <DropdownMenuSeparator className="bg-slate-700" />
              <DropdownMenuItem asChild className="text-slate-300 focus:text-white focus:bg-slate-800">
                <Link href="/admin">
                  <Shield className="w-4 h-4 mr-2" />
                  <span>Admin Dashboard</span>
                </Link>
              </DropdownMenuItem>
            </>
          )}
          <DropdownMenuSeparator className="bg-slate-700" />
          <DropdownMenuItem
            onClick={handleLogout}
            className="text-red-400 focus:text-red-300 focus:bg-slate-800"
          >
            <LogOut className="w-4 h-4 mr-2" />
            <span>Sign Out</span>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    )
  }

  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => setShowAuthDialog(true)}
        className="text-amber-400 hover:text-amber-300 min-h-[40px] px-2 sm:px-3"
      >
        <KeyRound className="w-4 h-4 sm:mr-2" />
        <span className="hidden sm:inline">Login</span>
      </Button>
      <AuthDialog
        open={showAuthDialog}
        onClose={() => setShowAuthDialog(false)}
        onSuccess={handleAuthSuccess}
      />
    </>
  )
}
