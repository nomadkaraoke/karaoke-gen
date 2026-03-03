"use client"

import { Suspense, useEffect, useState, useRef } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { Loader2, CheckCircle, XCircle } from "lucide-react"
import { Button } from "@/components/ui/button"
import { useAuth } from "@/lib/auth"

type VerifyState = "loading" | "success" | "error"

function VerifyMagicLinkContent() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const [state, setState] = useState<VerifyState>("loading")
  const [errorMessage, setErrorMessage] = useState("")
  const hasVerified = useRef(false)

  const { verifyMagicLink, user, error } = useAuth()

  useEffect(() => {
    // Prevent duplicate verification attempts
    if (hasVerified.current) return
    hasVerified.current = true

    const token = searchParams.get("token")

    if (!token) {
      setState("error")
      setErrorMessage("No verification token provided")
      return
    }

    // Verify the token
    const verify = async () => {
      const success = await verifyMagicLink(token)
      if (success) {
        setState("success")
        // Check if the verify response included a tenant subdomain
        // If we're on the wrong domain, redirect to the correct tenant portal
        const lastVerifyResponse = (window as any).__LAST_VERIFY_RESPONSE__
        if (lastVerifyResponse?.tenant_subdomain) {
          const currentHost = window.location.hostname.toLowerCase()
          const tenantSubdomain = lastVerifyResponse.tenant_subdomain.toLowerCase()
          if (currentHost !== tenantSubdomain) {
            // We're on the wrong domain — redirect to the correct tenant portal
            setTimeout(() => {
              window.location.href = `https://${tenantSubdomain}/app`
            }, 1500)
            return
          }
        }
        // Same domain or no tenant — redirect normally
        setTimeout(() => {
          router.push("/app")
        }, 2000)
      } else {
        setState("error")
        // Get error from auth store at this point
        const authStore = useAuth.getState()
        setErrorMessage(authStore.error || "Invalid or expired link")
      }
    }

    verify()
  }, [searchParams, verifyMagicLink, router])

  return (
    <div className="max-w-md w-full bg-card border border-border rounded-xl p-8 text-center">
      {state === "loading" && (
        <>
          <Loader2 className="w-12 h-12 text-primary animate-spin mx-auto mb-4" />
          <h1 className="text-xl font-semibold text-foreground mb-2">
            Verifying your sign-in link...
          </h1>
          <p className="text-muted-foreground">
            Please wait while we sign you in.
          </p>
        </>
      )}

      {state === "success" && (
        <>
          <CheckCircle className="w-12 h-12 text-success mx-auto mb-4" />
          <h1 className="text-xl font-semibold text-foreground mb-2">
            Successfully signed in!
          </h1>
          <p className="text-muted-foreground mb-4">
            Welcome back{user?.email ? `, ${user.email}` : ""}!
          </p>
          <p className="text-sm text-muted-foreground">
            Redirecting you to the app...
          </p>
        </>
      )}

      {state === "error" && (
        <>
          <XCircle className="w-12 h-12 text-destructive mx-auto mb-4" />
          <h1 className="text-xl font-semibold text-foreground mb-2">
            Sign-in failed
          </h1>
          <p className="text-muted-foreground mb-6">
            {errorMessage || "The link may have expired or already been used."}
          </p>
          <div className="space-y-2">
            <Button
              onClick={() => router.push("/app")}
              className="w-full bg-primary hover:bg-primary/90"
            >
              Go to App
            </Button>
            <p className="text-xs text-muted-foreground">
              You can request a new sign-in link from the app.
            </p>
          </div>
        </>
      )}
    </div>
  )
}

function VerifyLoadingFallback() {
  return (
    <div className="max-w-md w-full bg-card border border-border rounded-xl p-8 text-center">
      <Loader2 className="w-12 h-12 text-primary animate-spin mx-auto mb-4" />
      <h1 className="text-xl font-semibold text-foreground mb-2">
        Loading...
      </h1>
      <p className="text-muted-foreground">
        Please wait.
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
