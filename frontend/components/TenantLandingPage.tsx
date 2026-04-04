'use client'

import { useState } from 'react'
import { useRouter } from '@/i18n/routing'
import { TenantLogo } from '@/components/tenant-logo'
import { AuthDialog } from '@/components/auth/AuthDialog'
import { useTenant } from '@/lib/tenant'
import { ThemeToggle } from '@/components/ThemeToggle'
import { useTranslations } from 'next-intl'

/**
 * Minimal landing page for whitelabel tenant portals.
 * Shows tenant logo, brief description, and sign-in button.
 * No pricing, marketing content, or payment options.
 */
export function TenantLandingPage() {
  const t = useTranslations('tenant')
  const router = useRouter()
  const { tenant, branding } = useTenant()
  const [showAuthDialog, setShowAuthDialog] = useState(false)

  const handleAuthSuccess = () => {
    setShowAuthDialog(false)
    router.push('/app')
  }

  const tenantName = tenant?.name || 'Partner'
  const primaryColor = branding.primary_color || '#ff7acc'

  return (
    <div
      className="min-h-screen flex flex-col"
      style={{ backgroundColor: branding.background_color || '#000000' }}
    >
      {/* Header */}
      <header className="w-full px-6 py-4 flex items-center justify-between">
        <div className="w-8" /> {/* Spacer for centering */}
        <div /> {/* Center empty */}
        <div className="flex items-center gap-3">
          <ThemeToggle />
        </div>
      </header>

      {/* Main content - centered */}
      <main className="flex-1 flex flex-col items-center justify-center px-6 -mt-16">
        {/* Tenant logo */}
        <div className="mb-8">
          <TenantLogo size="lg" className="mx-auto" />
        </div>

        {/* Title */}
        <h1 className="text-2xl md:text-3xl font-semibold text-center mb-3" style={{ color: '#ffffff' }}>
          {t('title', { tenantName })}
        </h1>

        {/* Tagline */}
        {branding.tagline && (
          <p className="text-muted-foreground text-center text-lg mb-10 max-w-md">
            {branding.tagline}
          </p>
        )}

        {!branding.tagline && (
          <p className="text-muted-foreground text-center text-lg mb-10 max-w-md">
            {t('description')}
          </p>
        )}

        {/* Sign in button */}
        <button
          onClick={() => setShowAuthDialog(true)}
          className="px-8 py-3 rounded-lg text-lg font-semibold transition-all hover:opacity-90 cursor-pointer"
          style={{
            backgroundColor: primaryColor,
            color: 'var(--primary-foreground)',
          }}
        >
          {t('signIn')}
        </button>

        {/* Domain hint */}
        {(() => {
          const externalDomains = tenant?.allowed_email_domains?.filter(d => d !== 'nomadkaraoke.com') ?? []
          if (externalDomains.length === 0) return null
          return (
            <p className="text-muted-foreground text-sm mt-4">
              {t('signInWithDomain', { domains: externalDomains.map(d => `@${d}`).join(' or ') })}
            </p>
          )
        })()}
      </main>

      {/* Footer */}
      <footer className="w-full px-6 py-6 text-center">
        <p className="text-muted-foreground text-xs">
          <a
            href="https://gen.nomadkaraoke.com"
            className="underline hover:text-foreground transition-colors"
            target="_blank"
            rel="noopener noreferrer"
          >
            {t('poweredBy')}
          </a>
        </p>
      </footer>

      {/* Auth dialog */}
      <AuthDialog
        open={showAuthDialog}
        onClose={() => setShowAuthDialog(false)}
        onSuccess={handleAuthSuccess}
      />
    </div>
  )
}
