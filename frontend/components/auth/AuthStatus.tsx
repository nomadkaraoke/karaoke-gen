"use client"

import { useState, useEffect } from "react"
import { User, LogOut, CreditCard, Coins, KeyRound, Shield, FlaskConical, Gift } from "lucide-react"
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
import { Switch } from "@/components/ui/switch"
import { useAuth } from "@/lib/auth"
import { useAdminSettings } from "@/lib/admin-settings"
import { AuthDialog } from "./AuthDialog"
import { FeedbackDialog } from "@/components/feedback/FeedbackDialog"

interface AuthStatusProps {
  onAuthChange?: () => void
}

export function AuthStatus({ onAuthChange }: AuthStatusProps) {
  const { user, logout } = useAuth()
  const { showTestData, setShowTestData } = useAdminSettings()
  const [showAuthDialog, setShowAuthDialog] = useState(false)
  const [showFeedbackDialog, setShowFeedbackDialog] = useState(false)
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
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="sm"
            className="text-muted-foreground hover:text-foreground flex items-center gap-2 min-h-[40px] px-2 sm:px-3"
          >
            <User className="w-4 h-4" />
            <span className="hidden sm:inline max-w-[150px] truncate">
              {user.display_name || user.email}
            </span>
            <span className="flex items-center gap-1 text-warning font-medium">
              <Coins className="w-3 h-3" />
              {user.credits} {user.credits === 1 ? 'credit' : 'credits'}
            </span>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-56 bg-card border-border">
          <DropdownMenuLabel className="text-muted-foreground font-normal">
            <div className="flex flex-col space-y-1">
              <p className="text-sm font-medium text-foreground">
                {user.display_name || "Karaoke Fan"}
              </p>
              <p className="text-xs text-muted-foreground truncate">{user.email}</p>
            </div>
          </DropdownMenuLabel>
          <DropdownMenuSeparator className="bg-border" />
          <DropdownMenuItem
            className="text-muted-foreground focus:text-foreground focus:bg-secondary cursor-default"
            disabled
          >
            <Coins className="w-4 h-4 mr-2 text-warning" />
            <span>{user.credits} credits available</span>
          </DropdownMenuItem>
          <DropdownMenuItem
            onClick={handleBuyCredits}
            className="text-muted-foreground focus:text-foreground focus:bg-secondary"
          >
            <CreditCard className="w-4 h-4 mr-2" />
            <span>Buy More Credits</span>
          </DropdownMenuItem>
          {user.feedback_eligible && (
            <DropdownMenuItem
              onClick={() => setShowFeedbackDialog(true)}
              className="text-green-500 focus:text-green-400 focus:bg-secondary"
            >
              <Gift className="w-4 h-4 mr-2" />
              <span>Earn 2 Free Credits</span>
            </DropdownMenuItem>
          )}
          {(user.role === "admin" || user.email?.endsWith("@nomadkaraoke.com")) && (
            <>
              <DropdownMenuSeparator className="bg-border" />
              <DropdownMenuItem asChild className="text-muted-foreground focus:text-foreground focus:bg-secondary">
                <Link href="/admin">
                  <Shield className="w-4 h-4 mr-2" />
                  <span>Admin Dashboard</span>
                </Link>
              </DropdownMenuItem>
              <DropdownMenuItem
                onSelect={(e) => e.preventDefault()}
                className="text-muted-foreground focus:text-foreground focus:bg-secondary"
              >
                <FlaskConical className="w-4 h-4 mr-2" />
                <span className="flex-1">Show Test Jobs</span>
                <Switch
                  checked={showTestData}
                  onCheckedChange={setShowTestData}
                  className="ml-2"
                />
              </DropdownMenuItem>
            </>
          )}
          <DropdownMenuSeparator className="bg-border" />
          <DropdownMenuItem
            onClick={handleLogout}
            className="text-destructive focus:text-destructive focus:bg-secondary"
          >
            <LogOut className="w-4 h-4 mr-2" />
            <span>Sign Out</span>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <FeedbackDialog
        open={showFeedbackDialog}
        onClose={() => setShowFeedbackDialog(false)}
      />
    </>
    )
  }

  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => setShowAuthDialog(true)}
        className="text-warning hover:text-warning/80 min-h-[40px] px-2 sm:px-3"
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
