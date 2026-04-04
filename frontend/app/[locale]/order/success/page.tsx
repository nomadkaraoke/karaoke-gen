"use client"

import { Suspense } from "react"
import { useSearchParams } from "next/navigation"
import { CheckCircle, Loader2, Mail, Clock } from "lucide-react"
import { Button } from "@/components/ui/button"
import { useTranslations, useLocale } from "next-intl"

function OrderSuccessContent() {
  const t = useTranslations('order')
  const locale = useLocale()
  const searchParams = useSearchParams()
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

      <div className="space-y-4 mb-6">
        <div className="flex items-center gap-3 text-left p-3 bg-secondary rounded-lg">
          <Clock className="w-5 h-5 text-primary flex-shrink-0" />
          <div>
            <p className="font-medium text-foreground">{t('deliveryTime')}</p>
            <p className="text-sm text-muted-foreground">
              {t('deliveryNote')}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3 text-left p-3 bg-secondary rounded-lg">
          <Mail className="w-5 h-5 text-primary flex-shrink-0" />
          <div>
            <p className="font-medium text-foreground">{t('checkEmail')}</p>
            <p className="text-sm text-muted-foreground">
              {t('checkEmailNote')}
            </p>
          </div>
        </div>
      </div>

      <div className="space-y-3">
        <Button
          asChild
          className="w-full bg-primary hover:bg-primary/90"
        >
          <a href={`https://nomadkaraoke.com/${locale}/`}>
            {t('backToNomadKaraoke')}
          </a>
        </Button>

        <p className="text-xs text-muted-foreground">
          {t('questionsEmail')}
        </p>
      </div>
    </div>
  )
}

function OrderLoadingFallback() {
  const tCommon = useTranslations('common')
  return (
    <div className="max-w-md w-full bg-card border border-border rounded-xl p-8 text-center">
      <Loader2 className="w-16 h-16 text-primary mx-auto mb-4 animate-spin" />
      <p className="text-muted-foreground">{tCommon('loading')}</p>
    </div>
  )
}

export default function OrderSuccessPage() {
  return (
    <div className="min-h-screen bg-background flex items-center justify-center p-4">
      <Suspense fallback={<OrderLoadingFallback />}>
        <OrderSuccessContent />
      </Suspense>
    </div>
  )
}
