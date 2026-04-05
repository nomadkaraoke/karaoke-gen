'use client'

import { useState, useEffect } from 'react'
import { CreditCard, Loader2 } from 'lucide-react'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { api, CreditPackage } from '@/lib/api'
import { useAuth } from '@/lib/auth'
import { useTranslations } from 'next-intl'

interface BuyCreditsDialogProps {
  open: boolean
  onClose: () => void
}

const formatPrice = (cents: number) => `$${(cents / 100).toFixed(0)}`
const pricePerVideo = (pkg: CreditPackage) =>
  `$${(pkg.price_cents / 100 / pkg.credits).toFixed(2)}`

export function BuyCreditsDialog({ open, onClose }: BuyCreditsDialogProps) {
  const t = useTranslations('credits')
  const tCommon = useTranslations('common')
  const tRef = useTranslations('referrals')
  const { user } = useAuth()
  const [packages, setPackages] = useState<CreditPackage[]>([])
  const [selectedPackage, setSelectedPackage] = useState<CreditPackage | null>(null)
  const [loading, setLoading] = useState(false)
  const [checkoutLoading, setCheckoutLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open) return
    setError('')
    setLoading(true)
    api.getCreditPackages()
      .then((pkgs) => {
        setPackages(pkgs)
        // Pre-select best value package (5 credits) or first
        const bestValue = pkgs.find((p) => p.credits === 5) || pkgs[0]
        setSelectedPackage(bestValue)
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : 'Failed to load packages')
      })
      .finally(() => setLoading(false))
  }, [open])

  const handleCheckout = async () => {
    if (!selectedPackage || !user?.email) return
    setCheckoutLoading(true)
    setError('')
    try {
      const checkoutUrl = await api.createCheckout(selectedPackage.id, user.email)
      window.location.href = checkoutUrl
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create checkout session')
      setCheckoutLoading(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <DialogContent className="sm:max-w-md bg-card border-border">
        <DialogHeader>
          <DialogTitle className="text-foreground flex items-center gap-2">
            <CreditCard className="w-5 h-5 text-primary" />
            {t('title')}
          </DialogTitle>
          <DialogDescription className="text-muted-foreground">
            {t('description')}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 mt-2">
          {user?.has_active_referral_discount && user?.referral_discount_percent && (
            <div className="bg-green-500/10 border border-green-500/20 rounded-lg p-3 text-center">
              <p className="text-green-600 dark:text-green-400 text-sm font-medium">
                {tRef('discountBadge', { percent: user.referral_discount_percent })}
              </p>
              {user.referral_discount_expires_at && (
                <p className="text-green-600/70 dark:text-green-400/70 text-xs mt-1">
                  Applied automatically at checkout · Expires {new Date(user.referral_discount_expires_at).toLocaleDateString()}
                </p>
              )}
            </div>
          )}

          {loading && (
            <div className="flex justify-center py-8">
              <Loader2 className="w-6 h-6 animate-spin text-muted-foreground" />
            </div>
          )}

          {error && (
            <div className="text-center py-4 text-sm text-destructive">{error}</div>
          )}

          {!loading && !error && packages.length > 0 && (
            <>
              <div className="grid grid-cols-2 gap-3">
                {packages.map((pkg) => {
                  const isSelected = selectedPackage?.id === pkg.id
                  const isBestValue = pkg.credits === 5
                  const savings = pkg.credits > 1
                    ? Math.round((1 - pkg.price_cents / (pkg.credits * 1000)) * 100)
                    : 0

                  return (
                    <button
                      key={pkg.id}
                      onClick={() => setSelectedPackage(pkg)}
                      className={`relative p-4 rounded-xl border-2 transition-all text-left ${
                        isSelected
                          ? 'border-primary bg-primary/10'
                          : 'border-border hover:border-muted-foreground/50 bg-secondary/50'
                      }`}
                    >
                      {isBestValue && (
                        <span className="absolute -top-2.5 left-1/2 -translate-x-1/2 bg-primary text-primary-foreground text-xs font-semibold px-2 py-0.5 rounded-full">
                          {t('bestValue')}
                        </span>
                      )}
                      <div className="text-2xl font-bold">{pkg.credits}</div>
                      <div className="text-sm text-muted-foreground mb-1">
                        {pkg.credits === 1 ? tCommon('credit') : tCommon('credits')}
                      </div>
                      <div className="text-lg font-semibold text-primary">
                        {formatPrice(pkg.price_cents)}
                      </div>
                      <div className="text-xs text-muted-foreground">{t('pricePerVideo', { price: pricePerVideo(pkg) })}</div>
                      {savings > 0 && (
                        <div className="text-xs text-green-500 mt-1">{t('savePct', { savings })}</div>
                      )}
                    </button>
                  )
                })}
              </div>

              <Button
                onClick={handleCheckout}
                disabled={!selectedPackage || checkoutLoading}
                className="w-full"
              >
                {checkoutLoading ? (
                  <>
                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                    {t('redirecting')}
                  </>
                ) : selectedPackage ? (
                  t('continuePayment', { price: formatPrice(selectedPackage.price_cents) })
                ) : (
                  t('selectPackage')
                )}
              </Button>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
