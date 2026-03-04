"use client"

import { useRouter } from "next/navigation"
import { useAuth } from "@/lib/auth"
import { Logo } from "./logo"
import { Button } from "./ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "./ui/dropdown-menu"
import { User, LogOut, Settings, Shield, Mail, Phone, HelpCircle } from "lucide-react"
import Link from "next/link"

export function AppHeader() {
  const { user, logout } = useAuth()
  const router = useRouter()

  const handleLogout = () => {
    logout()
    router.push("/")
  }

  if (!user) return null

  return (
    <header className="sticky top-0 z-50 border-b border-border/50 bg-background/80 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="container mx-auto px-4 h-16 flex items-center justify-between">
        <Link href="/dashboard" className="h-10">
          <Logo className="h-10" />
        </Link>

        <nav className="hidden md:flex items-center gap-6">
          <Link href="/dashboard" className="text-sm font-medium hover:text-primary transition-colors">
            Dashboard
          </Link>
          <Link href="/jobs" className="text-sm font-medium hover:text-primary transition-colors">
            My Jobs
          </Link>
          {(user.role === "admin" || user.email?.endsWith("@nomadkaraoke.com")) && (
            <Link href="/admin" className="text-sm font-medium hover:text-primary transition-colors">
              Admin
            </Link>
          )}
        </nav>

        <div className="flex items-center gap-4">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button className="hidden lg:flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors">
                <HelpCircle className="w-3.5 h-3.5" />
                <span>Need help?</span>
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-64">
              <DropdownMenuLabel className="text-sm">
                Something confusing or not working? Reach out anytime!
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuItem asChild>
                <a href="mailto:andrew@nomadkaraoke.com">
                  <Mail className="w-4 h-4 mr-2" />
                  andrew@nomadkaraoke.com
                </a>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <a href="tel:+18036363267">
                  <Phone className="w-4 h-4 mr-2" />
                  +1 (803) 636-3267
                </a>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          <div className="hidden sm:flex items-center gap-2 px-4 py-2 rounded-lg bg-primary/10 border border-primary/20">
            <div className="w-2 h-2 rounded-full bg-primary animate-pulse" />
            <span className="text-sm font-medium text-primary">{user.credits} credits</span>
          </div>

          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" className="rounded-full">
                <User className="w-5 h-5" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56">
              <DropdownMenuLabel>
                <div className="flex flex-col gap-1">
                  <p className="text-sm font-medium">Account</p>
                  <p className="text-xs text-muted-foreground">{user.role === "admin" ? "Admin" : "User"}</p>
                </div>
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuItem className="sm:hidden">
                <div className="flex items-center gap-2">
                  <div className="w-2 h-2 rounded-full bg-primary" />
                  <span>{user.credits} credits</span>
                </div>
              </DropdownMenuItem>
              {(user.role === "admin" || user.email?.endsWith("@nomadkaraoke.com")) && (
                <DropdownMenuItem asChild>
                  <Link href="/admin">
                    <Shield className="w-4 h-4 mr-2" />
                    Admin Dashboard
                  </Link>
                </DropdownMenuItem>
              )}
              <DropdownMenuItem>
                <Settings className="w-4 h-4 mr-2" />
                Settings
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <a href="mailto:andrew@nomadkaraoke.com">
                  <Mail className="w-4 h-4 mr-2" />
                  andrew@nomadkaraoke.com
                </a>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <a href="tel:+18036363267">
                  <Phone className="w-4 h-4 mr-2" />
                  +1 (803) 636-3267
                </a>
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={handleLogout} className="text-destructive focus:text-destructive">
                <LogOut className="w-4 h-4 mr-2" />
                Logout
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
    </header>
  )
}
