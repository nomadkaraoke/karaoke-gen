"use client"

import { Link } from "@/i18n/routing"
import { Button } from "./ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "./ui/dropdown-menu"
import { HelpCircle, Mail, Phone, Users, Moon, Sun } from "lucide-react"
import LanguageSwitcher from "./LanguageSwitcher"
import { AuthStatus } from "./auth"
import { TenantLogo } from "./tenant-logo"
import { useTranslations } from "next-intl"
import { useTenant } from "@/lib/tenant"
import { useTheme } from "@/lib/theme"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "./ui/tooltip"
import { ReactNode, useState, useEffect } from "react"

/**
 * Shared app header used across all /app/* pages.
 * Matches the dashboard header style. Pass page-specific
 * buttons via the `children` prop (rendered before the
 * standard right-side controls).
 *
 * Mobile: two rows — row 1 (brand + auth), row 2 (actions with text labels).
 * Desktop (sm+): single row as before.
 */
export function AppHeader({ children }: { children?: ReactNode }) {
  const t = useTranslations('dashboard')
  const tHeader = useTranslations('header')
  const { branding, isDefault: isDefaultTenant } = useTenant()
  const { isDarkMode, toggleTheme } = useTheme()
  const [mounted, setMounted] = useState(false)

  useEffect(() => { setMounted(true) }, [])

  return (
    <header className="fixed top-0 left-0 right-0 z-50 bg-dark-900/80 backdrop-blur-md border-b border-dark-700">
      {/* Desktop: single row (sm+) */}
      <div className="hidden sm:flex px-4 py-3 items-center justify-between gap-2">
        <div className="flex items-center gap-3 min-w-0">
          <Link href="/app">
            {isDefaultTenant ? (
              /* eslint-disable-next-line @next/next/no-img-element */
              <img src="/nomad-karaoke-logo.svg" alt="Nomad Karaoke" className="h-10 shrink-0" />
            ) : (
              <TenantLogo size="sm" />
            )}
          </Link>
          <div className="min-w-0">
            <Link href="/app" className="text-xl font-bold truncate block" style={{ color: 'var(--text)' }}>
              {isDefaultTenant ? t('title') : branding.site_title}
            </Link>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {children}
          <HelpDropdown tHeader={tHeader} />
          <Link href="/app/referrals" className="flex items-center gap-1.5 text-sm hover:underline min-h-[40px] px-3" style={{ color: 'var(--text-muted)' }}>
            <Users className="w-4 h-4" />
            <span>{t('referrals')}</span>
          </Link>
          <LanguageSwitcher />
          <AuthStatus />
          {mounted && <ThemeToggle isDarkMode={isDarkMode} toggleTheme={toggleTheme} t={t} />}
        </div>
      </div>

      {/* Mobile: two rows (< sm) */}
      <div className="sm:hidden">
        {/* Row 1: Brand + Auth */}
        <div className="px-3 py-2 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <Link href="/app">
              {isDefaultTenant ? (
                /* eslint-disable-next-line @next/next/no-img-element */
                <img src="/nomad-karaoke-logo.svg" alt="Nomad Karaoke" className="h-8 shrink-0" />
              ) : (
                <TenantLogo size="sm" />
              )}
            </Link>
            <div className="min-w-0">
              <Link href="/app" className="text-base font-bold truncate block" style={{ color: 'var(--text)' }}>
                {isDefaultTenant ? t('title') : branding.site_title}
              </Link>
            </div>
          </div>
          <AuthStatus />
        </div>
        {/* Row 2: Action buttons with text labels */}
        <div className="px-3 pb-2 flex items-center gap-1 flex-wrap">
          {children}
          <HelpDropdown tHeader={tHeader} showLabel />
          <Link href="/app/referrals" className="flex items-center gap-1.5 text-sm hover:underline min-h-[36px] px-2" style={{ color: 'var(--text-muted)' }}>
            <Users className="w-4 h-4" />
            <span>{t('referrals')}</span>
          </Link>
          <LanguageSwitcher showLabel />
          {mounted && <ThemeToggle isDarkMode={isDarkMode} toggleTheme={toggleTheme} t={t} />}
        </div>
      </div>
    </header>
  )
}

function HelpDropdown({ tHeader, showLabel }: { tHeader: ReturnType<typeof useTranslations>; showLabel?: boolean }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="min-h-[40px] px-2 sm:px-3"
          style={{ color: 'var(--text-muted)' }}
        >
          <HelpCircle className="w-4 h-4" />
          {showLabel && <span className="ms-1.5 text-sm">{tHeader('needHelp')}</span>}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuLabel className="text-sm font-normal">
          {tHeader('helpPrompt')}
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <a href="mailto:andrew@nomadkaraoke.com">
            <Mail className="w-4 h-4 me-2" />
            andrew@nomadkaraoke.com
          </a>
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <a href="tel:+18036363267">
            <Phone className="w-4 h-4 me-2" />
            +1 (803) 636-3267
          </a>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function ThemeToggle({ isDarkMode, toggleTheme, t }: { isDarkMode: boolean; toggleTheme: () => void; t: ReturnType<typeof useTranslations> }) {
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            variant="ghost"
            size="sm"
            onClick={toggleTheme}
            className="min-h-[40px] px-2 sm:px-3"
            style={{ color: 'var(--text-muted)' }}
          >
            {isDarkMode ? (
              <Sun className="w-4 h-4" />
            ) : (
              <Moon className="w-4 h-4" />
            )}
          </Button>
        </TooltipTrigger>
        <TooltipContent side="bottom">
          <p>{isDarkMode ? t('switchToLightMode') : t('switchToDarkMode')}</p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
