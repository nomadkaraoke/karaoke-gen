"use client"

import { Suspense, useEffect, useState } from "react"
import { useRouter, useSearchParams } from "next/navigation"
import { CheckCircle, Loader2, Coins } from "lucide-react"
import { Button } from "@/components/ui/button"
import { useAuth } from "@/lib/auth"

function PaymentSuccessContent() {
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
        Payment Successful!
      </h1>

      <p className="text-muted-foreground mb-6">
        Thank you for your purchase. Your credits have been added to your account.
      </p>

      {isLoading ? (
        <div className="flex items-center justify-center gap-2 text-muted-foreground mb-6">
          <Loader2 className="w-4 h-4 animate-spin" />
          Loading your balance...
        </div>
      ) : user ? (
        <div className="bg-secondary rounded-lg p-4 mb-6">
          <div className="flex items-center justify-center gap-2 text-warning">
            <Coins className="w-5 h-5" />
            <span className="text-2xl font-bold">{user.credits}</span>
            <span className="text-muted-foreground">credits available</span>
          </div>
        </div>
      ) : null}

      <div className="space-y-3">
        <Button
          onClick={() => router.push("/app")}
          className="w-full bg-primary hover:bg-primary/90"
        >
          Start Creating Karaoke
        </Button>

        <p className="text-xs text-muted-foreground">
          A confirmation email has been sent to your email address.
        </p>
      </div>
    </div>
  )
}

export default function PaymentSuccessPage() {
  return (
    <div className="min-h-screen bg-background flex items-center justify-center p-4">
      <Suspense
        fallback={
          <div className="max-w-md w-full bg-card border border-border rounded-xl p-8 text-center">
            <Loader2 className="w-16 h-16 text-primary mx-auto mb-4 animate-spin" />
            <p className="text-muted-foreground">Loading...</p>
          </div>
        }
      >
        <PaymentSuccessContent />
      </Suspense>
    </div>
  )
}
