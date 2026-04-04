"use client"

import { useRouter, Link } from "@/i18n/routing"
import NextLink from "next/link"
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
import { User, LogOut, Settings, Shield, Mail, Phone, HelpCircle, Eye } from "lucide-react"
import LanguageSwitcher from "./LanguageSwitcher"
import { useTranslations } from "next-intl"

export function AppHeader() {
  const t = useTranslations('header')
  const { user, logout, isImpersonating, impersonatedUserEmail, endImpersonation } = useAuth()
  const router = useRouter()

  const handleLogout = () => {
    logout()
    router.push("/")
  }

  const handleBackToAdmin = () => {
    endImpersonation()
    window.location.href = "/admin"
  }

  if (!user) return null

  return (
    <header className="sticky top-0 z-50 border-b border-border/50 bg-background/80 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="container mx-auto px-4 h-16 flex items-center justify-between">
        <Link href="/app" className="h-10">
          <Logo className="h-10" />
        </Link>

        <nav className="hidden md:flex items-center gap-6">
          <Link href="/app" className="text-sm font-medium hover:text-primary transition-colors">
            {t('dashboard')}
          </Link>
          <Link href="/jobs" className="text-sm font-medium hover:text-primary transition-colors">
            {t('myJobs')}
          </Link>
          {(user.role === "admin" || user.email?.endsWith("@nomadkaraoke.com")) && (
            <NextLink href="/admin" className="text-sm font-medium hover:text-primary transition-colors">
              {t('admin')}
            </NextLink>
          )}
        </nav>

        <div className="flex items-center gap-4">
          <LanguageSwitcher />

          {isImpersonating && (
            <Button
              variant="outline"
              size="sm"
              onClick={handleBackToAdmin}
              className="h-8 gap-1.5 border-purple-500/40 text-purple-300 hover:text-white hover:bg-purple-800/40"
            >
              <Shield className="w-3.5 h-3.5" />
              {t('backToAdmin')}
            </Button>
          )}

          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button className="hidden lg:flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors">
                <HelpCircle className="w-3.5 h-3.5" />
                <span>{t('needHelp')}</span>
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-64">
              <DropdownMenuLabel className="text-sm">
                {t('helpPrompt')}
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
            <span className="text-sm font-medium text-primary">{t('credits', { count: user.credits })}</span>
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
                  <p className="text-sm font-medium">{t('account')}</p>
                  <p className="text-xs text-muted-foreground">{user.role === "admin" ? t('adminRole') : t('userRole')}</p>
                </div>
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuItem className="sm:hidden">
                <div className="flex items-center gap-2">
                  <div className="w-2 h-2 rounded-full bg-primary" />
                  <span>{t('credits', { count: user.credits })}</span>
                </div>
              </DropdownMenuItem>
              {(user.role === "admin" || user.email?.endsWith("@nomadkaraoke.com")) && (
                <DropdownMenuItem asChild>
                  <NextLink href="/admin">
                    <Shield className="w-4 h-4 mr-2" />
                    {t('adminDashboard')}
                  </NextLink>
                </DropdownMenuItem>
              )}
              <DropdownMenuItem>
                <Settings className="w-4 h-4 mr-2" />
                {t('settings')}
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
                {t('logout')}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
    </header>
  )
}
