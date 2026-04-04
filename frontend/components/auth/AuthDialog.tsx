"use client"

import { useState } from "react"
import { Mail, KeyRound, ArrowLeft, Loader2, CheckCircle } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { useAuth } from "@/lib/auth"
import { useTranslations } from "next-intl"

interface AuthDialogProps {
  open: boolean
  onClose: () => void
  onSuccess: () => void
}

type AuthStep = "email" | "sent" | "token"

export function AuthDialog({ open, onClose, onSuccess }: AuthDialogProps) {
  const t = useTranslations('auth.dialog')
  const tCommon = useTranslations('common')
  const [step, setStep] = useState<AuthStep>("email")
  const [email, setEmail] = useState("")
  const [token, setToken] = useState("")
  const [localError, setLocalError] = useState("")

  const { sendMagicLink, loginWithToken, isLoading, error, clearError } = useAuth()

  const handleSendMagicLink = async (e: React.FormEvent) => {
    e.preventDefault()
    setLocalError("")

    if (!email.trim()) {
      setLocalError("Please enter your email")
      return
    }

    // Simple email validation
    if (!email.includes("@") || !email.includes(".")) {
      setLocalError("Please enter a valid email")
      return
    }

    const success = await sendMagicLink(email.trim().toLowerCase())
    if (success) {
      setStep("sent")
    }
  }

  const handleTokenSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLocalError("")

    if (!token.trim()) {
      setLocalError("Please enter your access token")
      return
    }

    const success = await loginWithToken(token.trim())
    if (success) {
      setToken("") // Clear for security
      onSuccess()
    }
  }

  const handleClose = () => {
    setStep("email")
    setEmail("")
    setToken("")
    setLocalError("")
    clearError()
    onClose()
  }

  const displayError = localError || error

  return (
    <Dialog open={open} onOpenChange={(open) => !open && handleClose()}>
      <DialogContent className="sm:max-w-md bg-card border-border">
        {step === "email" && (
          <>
            <DialogHeader>
              <DialogTitle className="text-foreground flex items-center gap-2">
                <Mail className="w-5 h-5 text-primary" />
                {t('title')}
              </DialogTitle>
              <DialogDescription className="text-muted-foreground">
                {t('emailPrompt')}
              </DialogDescription>
            </DialogHeader>
            <form onSubmit={handleSendMagicLink} className="space-y-4">
              <div>
                <Input
                  type="email"
                  placeholder={t('emailPlaceholder')}
                  value={email}
                  onChange={(e) => {
                    setEmail(e.target.value)
                    setLocalError("")
                    clearError()
                  }}
                  className="bg-secondary border-border text-foreground"
                  autoFocus
                  disabled={isLoading}
                />
                {displayError && (
                  displayError === "disposable_email_not_allowed" ? (
                    <div className="mt-2 p-3 rounded-md bg-amber-500/10 border border-amber-500/30 text-sm space-y-2">
                      <p className="font-medium text-amber-400">{t('disposableEmailWarning')}</p>
                      <p className="text-muted-foreground">
                        {t('disposableEmailExplanation')}
                      </p>
                      <p className="text-muted-foreground">
                        {t('noSpamPromise')}
                      </p>
                    </div>
                  ) : (
                    <p className="text-xs text-destructive mt-1">{displayError}</p>
                  )
                )}
              </div>
              <div className="flex flex-col gap-2">
                <Button
                  type="submit"
                  disabled={!email.trim() || isLoading}
                  className="w-full bg-primary hover:bg-primary/90"
                >
                  {isLoading ? (
                    <>
                      <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                      {t('sending')}
                    </>
                  ) : (
                    t('sendSignInLink')
                  )}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => setStep("token")}
                  className="text-muted-foreground hover:text-foreground"
                >
                  <KeyRound className="w-4 h-4 mr-2" />
                  {t('useAccessToken')}
                </Button>
              </div>
              <p className="text-xs text-muted-foreground text-center">
                {t('noAccount')}
              </p>
            </form>
          </>
        )}

        {step === "sent" && (
          <>
            <DialogHeader>
              <DialogTitle className="text-foreground flex items-center gap-2">
                <CheckCircle className="w-5 h-5 text-success" />
                {t('checkEmailTitle')}
              </DialogTitle>
            </DialogHeader>
            <div className="space-y-4 py-4">
              <p className="text-foreground">
                {t('checkEmailSent')}
              </p>
              <p className="text-foreground font-medium bg-secondary px-4 py-2 rounded-md">
                {email}
              </p>
              <p className="text-sm text-muted-foreground">
                {t('checkEmailExpiry')}
              </p>
              <div className="pt-4 border-t border-border space-y-2">
                <Button
                  variant="outline"
                  onClick={() => setStep("email")}
                  className="w-full border-border text-foreground hover:text-foreground"
                >
                  <ArrowLeft className="w-4 h-4 mr-2" />
                  {t('tryDifferentEmail')}
                </Button>
                <Button
                  variant="ghost"
                  onClick={handleClose}
                  className="w-full text-muted-foreground"
                >
                  {tCommon('close')}
                </Button>
              </div>
            </div>
          </>
        )}

        {step === "token" && (
          <>
            <DialogHeader>
              <DialogTitle className="text-foreground flex items-center gap-2">
                <KeyRound className="w-5 h-5 text-warning" />
                {t('accessTokenTitle')}
              </DialogTitle>
              <DialogDescription className="text-muted-foreground">
                {t('accessTokenPrompt')}
              </DialogDescription>
            </DialogHeader>
            <form onSubmit={handleTokenSubmit} className="space-y-4">
              <div>
                <Input
                  type="password"
                  placeholder={t('accessTokenPlaceholder')}
                  value={token}
                  onChange={(e) => {
                    setToken(e.target.value)
                    setLocalError("")
                    clearError()
                  }}
                  className="bg-secondary border-border text-foreground"
                  autoFocus
                  disabled={isLoading}
                />
                {displayError && (
                  <p className="text-xs text-destructive mt-1">{displayError}</p>
                )}
              </div>
              <div className="flex flex-col gap-2">
                <Button
                  type="submit"
                  disabled={!token.trim() || isLoading}
                  className="w-full bg-warning hover:bg-warning/90 text-warning-foreground"
                >
                  {isLoading ? (
                    <>
                      <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                      {t('authenticating')}
                    </>
                  ) : (
                    t('authenticate')
                  )}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => setStep("email")}
                  className="text-muted-foreground hover:text-foreground"
                >
                  <ArrowLeft className="w-4 h-4 mr-2" />
                  {t('backToEmail')}
                </Button>
              </div>
              <p className="text-xs text-muted-foreground text-center">
                {t('contactAdmin')}
              </p>
            </form>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
