"use client"

import { Suspense, useEffect, useState, useRef } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { Loader2, CheckCircle, XCircle, Gift, ShoppingCart } from "lucide-react"
import { Button } from "@/components/ui/button"
import { useAuth } from "@/lib/auth"
import { useTranslations } from "next-intl"

type VerifyState = "verifying" | "preparing" | "credits_granted" | "credits_denied" | "credits_pending" | "success" | "error"

function VerifyMagicLinkContent() {
  const t = useTranslations('auth')
  const router = useRouter()
  const searchParams = useSearchParams()
  const [state, setState] = useState<VerifyState>("verifying")
  const [errorMessage, setErrorMessage] = useState("")
  const [creditsGranted, setCreditsGranted] = useState(0)
  const [creditStatus, setCreditStatus] = useState<string>("not_applicable")
  const hasVerified = useRef(false)

  const { verifyMagicLink, user, error } = useAuth()

  useEffect(() => {
    if (hasVerified.current) return
    hasVerified.current = true

    const token = searchParams.get("token")

    if (!token) {
      setState("error")
      setErrorMessage(t('noVerificationToken'))
      return
    }

    const verify = async () => {
      // Show "preparing your account" while the backend runs AI evaluation
      setState("preparing")

      const success = await verifyMagicLink(token)
      if (success) {
        const lastVerifyResponse = (window as any).__LAST_VERIFY_RESPONSE__
        const credits = lastVerifyResponse?.credits_granted || 0
        const status = lastVerifyResponse?.credit_status || "not_applicable"
        setCreditsGranted(credits)
        setCreditStatus(status)

        // Check for tenant subdomain redirect
        if (lastVerifyResponse?.tenant_subdomain) {
          const currentHost = window.location.hostname.toLowerCase()
          const tenantSubdomain = lastVerifyResponse.tenant_subdomain.toLowerCase()
          if (currentHost !== tenantSubdomain) {
            setState("success")
            setTimeout(() => {
              window.location.href = `https://${tenantSubdomain}/app`
            }, 1500)
            return
          }
        }

        // Show appropriate interstitial based on credit status
        if (status === "granted" && credits > 0) {
          setState("credits_granted")
        } else if (status === "denied") {
          setState("credits_denied")
        } else if (status === "pending_review") {
          setState("credits_pending")
        } else {
          // Returning user or not applicable — go straight to app
          setState("success")
          setTimeout(() => {
            router.push("/app")
          }, 1500)
        }
      } else {
        setState("error")
        const authStore = useAuth.getState()
        setErrorMessage(authStore.error || t('linkExpiredOrUsed'))
      }
    }

    verify()
  }, [searchParams, verifyMagicLink, router, t])

  const goToApp = () => router.push("/app")
  const goToBuy = () => router.push("/app?buy=true")

  return (
    <div className="max-w-md w-full bg-card border border-border rounded-xl p-8 text-center">
      {/* Verifying token */}
      {state === "verifying" && (
        <>
          <Loader2 className="w-12 h-12 text-primary animate-spin mx-auto mb-4" />
          <h1 className="text-xl font-semibold text-foreground mb-2">
            {t('verifying')}
          </h1>
          <p className="text-muted-foreground">
            {t('verifyingWait')}
          </p>
        </>
      )}

      {/* Preparing account (AI evaluation running) */}
      {state === "preparing" && (
        <>
          <Loader2 className="w-12 h-12 text-primary animate-spin mx-auto mb-4" />
          <h1 className="text-xl font-semibold text-foreground mb-2">
            {t('preparingAccount')}
          </h1>
          <p className="text-muted-foreground">
            {t('preparingWait')}
          </p>
        </>
      )}

      {/* Credits granted - celebratory interstitial */}
      {state === "credits_granted" && (
        <>
          <div className="relative mx-auto mb-4 w-16 h-16">
            <Gift className="w-16 h-16 text-primary" />
          </div>
          <h1 className="text-2xl font-bold text-foreground mb-2">
            {t('welcomeTitle')}
          </h1>
          <div className="bg-primary/10 border border-primary/20 rounded-lg p-4 mb-4">
            <p className="text-3xl font-bold text-primary">{creditsGranted}</p>
            <p className="text-sm font-medium text-primary/80">{t('freeCreditsGranted', { count: creditsGranted })}</p>
          </div>
          <p className="text-sm text-muted-foreground mb-2">
            {t('freeCreditsExplanation')}
          </p>
          <p className="text-xs text-muted-foreground mb-6">
            {t('freeCreditsRequest')}
          </p>
          <Button
            onClick={goToApp}
            className="w-full bg-primary hover:bg-primary/90"
          >
            {t('startCreating')}
          </Button>
        </>
      )}

      {/* Credits denied - friendly rejection interstitial */}
      {state === "credits_denied" && (
        <>
          <XCircle className="w-12 h-12 text-muted-foreground mx-auto mb-4" />
          <h1 className="text-xl font-semibold text-foreground mb-2">
            {t('welcomeBack')}
          </h1>
          <p className="text-muted-foreground mb-4">
            {t('creditsDeniedExplanation')}
          </p>
          <p className="text-sm text-muted-foreground mb-6">
            {t('creditsDeniedHelp')}
          </p>
          <div className="space-y-2">
            <Button
              onClick={goToBuy}
              className="w-full bg-primary hover:bg-primary/90"
            >
              <ShoppingCart className="w-4 h-4 mr-2" />
              {t('buyCredits')}
            </Button>
            <Button
              onClick={goToApp}
              variant="ghost"
              className="w-full"
            >
              {t('goToDashboard')}
            </Button>
          </div>
        </>
      )}

      {/* Credits pending review - friendly holding message */}
      {state === "credits_pending" && (
        <>
          <Loader2 className="w-12 h-12 text-muted-foreground mx-auto mb-4" />
          <h1 className="text-xl font-semibold text-foreground mb-2">
            {t('creditsPendingTitle')}
          </h1>
          <p className="text-muted-foreground mb-4">
            {t('creditsPendingExplanation')}
          </p>
          <p className="text-sm text-muted-foreground mb-6">
            {t('creditsPendingHint')}
          </p>
          <div className="space-y-2">
            <Button
              onClick={goToBuy}
              className="w-full bg-primary hover:bg-primary/90"
            >
              <ShoppingCart className="w-4 h-4 mr-2" />
              {t('buyCredits')}
            </Button>
            <Button
              onClick={goToApp}
              variant="ghost"
              className="w-full"
            >
              {t('exploreApp')}
            </Button>
          </div>
        </>
      )}

      {/* Simple success (returning user) */}
      {state === "success" && (
        <>
          <CheckCircle className="w-12 h-12 text-success mx-auto mb-4" />
          <h1 className="text-xl font-semibold text-foreground mb-2">
            {t('successSignedIn')}
          </h1>
          <p className="text-muted-foreground mb-4">
            {t('welcomeBackUser', { email: user?.email ? `, ${user.email}` : "" })}
          </p>
          <p className="text-sm text-muted-foreground">
            {t('redirectingToApp')}
          </p>
        </>
      )}

      {/* Error */}
      {state === "error" && (
        <>
          <XCircle className="w-12 h-12 text-destructive mx-auto mb-4" />
          <h1 className="text-xl font-semibold text-foreground mb-2">
            {t('signInFailed')}
          </h1>
          <p className="text-muted-foreground mb-6">
            {errorMessage || t('linkExpiredOrUsed')}
          </p>
          <div className="space-y-2">
            <Button
              onClick={() => router.push("/app")}
              className="w-full bg-primary hover:bg-primary/90"
            >
              {t('goToApp')}
            </Button>
            <p className="text-xs text-muted-foreground">
              {t('requestNewLink')}
            </p>
          </div>
        </>
      )}
    </div>
  )
}

function VerifyLoadingFallback() {
  const t = useTranslations('auth')
  return (
    <div className="max-w-md w-full bg-card border border-border rounded-xl p-8 text-center">
      <Loader2 className="w-12 h-12 text-primary animate-spin mx-auto mb-4" />
      <h1 className="text-xl font-semibold text-foreground mb-2">
        {t('pleaseWait')}
      </h1>
      <p className="text-muted-foreground">
        {t('pleaseWait')}
      </p>
    </div>
  )
}

export default function VerifyMagicLinkPage() {
  return (
    <div className="min-h-screen bg-background flex items-center justify-center p-4">
      <Suspense fallback={<VerifyLoadingFallback />}>
        <VerifyMagicLinkContent />
      </Suspense>
    </div>
  )
}
