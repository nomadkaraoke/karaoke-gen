"use client"

import { Suspense, useEffect, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { CheckCircle, Loader2, Coins } from "lucide-react"
import { Button } from "@/components/ui/button"
import { useAuth } from "@/lib/auth"
import { useTranslations } from "next-intl"

function PaymentSuccessContent() {
  const t = useTranslations('payment')
  const router = useRouter()
  const searchParams = useSearchParams()
  const [isLoading, setIsLoading] = useState(true)

  const { user, fetchUser } = useAuth()

  useEffect(() => {
    // Refresh user data to get updated credits
    const refreshUser = async () => {
      await fetchUser()
      setIsLoading(false)
    }
    refreshUser()
  }, [fetchUser])

  const sessionId = searchParams.get("session_id")

  return (
    <div className="max-w-md w-full bg-card border border-border rounded-xl p-8 text-center">
      <CheckCircle className="w-16 h-16 text-success mx-auto mb-4" />

      <h1 className="text-2xl font-bold text-foreground mb-2">
        {t('title')}
      </h1>

      <p className="text-muted-foreground mb-6">
        {t('description')}
      </p>

      {isLoading ? (
        <div className="flex items-center justify-center gap-2 text-muted-foreground mb-6">
          <Loader2 className="w-4 h-4 animate-spin" />
          {t('loadingBalance')}
        </div>
      ) : user ? (
        <div className="bg-secondary rounded-lg p-4 mb-6">
          <div className="flex items-center justify-center gap-2 text-warning">
            <Coins className="w-5 h-5" />
            <span className="text-2xl font-bold">{user.credits}</span>
            <span className="text-muted-foreground">{t('creditsAvailable')}</span>
          </div>
        </div>
      ) : null}

      <div className="space-y-3">
        <Button
          onClick={() => router.push("/app")}
          className="w-full bg-primary hover:bg-primary/90"
        >
          {t('startCreating')}
        </Button>

        <p className="text-xs text-muted-foreground">
          {t('confirmationEmail')}
        </p>
      </div>
    </div>
  )
}

function PaymentLoadingFallback() {
  const tCommon = useTranslations('common')
  return (
    <div className="max-w-md w-full bg-card border border-border rounded-xl p-8 text-center">
      <Loader2 className="w-16 h-16 text-primary mx-auto mb-4 animate-spin" />
      <p className="text-muted-foreground">{tCommon('loading')}</p>
    </div>
  )
}

export default function PaymentSuccessPage() {
  return (
    <div className="min-h-screen bg-background flex items-center justify-center p-4">
      <Suspense fallback={<PaymentLoadingFallback />}>
        <PaymentSuccessContent />
      </Suspense>
    </div>
  )
}
