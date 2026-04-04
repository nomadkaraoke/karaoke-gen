"use client"

import { Suspense } from "react"
import { useSearchParams } from "next/navigation"
import { CheckCircle, Loader2, Mail, Clock } from "lucide-react"
import { Button } from "@/components/ui/button"

function OrderSuccessContent() {
  const searchParams = useSearchParams()
  const sessionId = searchParams.get("session_id")

  return (
    <div className="max-w-md w-full bg-card border border-border rounded-xl p-8 text-center">
      <CheckCircle className="w-16 h-16 text-success mx-auto mb-4" />

      <h1 className="text-2xl font-bold text-foreground mb-2">
        Order Confirmed!
      </h1>

      <p className="text-muted-foreground mb-6">
        Thank you for your order. We&apos;re creating your custom karaoke video now.
      </p>

      <div className="space-y-4 mb-6">
        <div className="flex items-center gap-3 text-left p-3 bg-secondary rounded-lg">
          <Clock className="w-5 h-5 text-primary flex-shrink-0" />
          <div>
            <p className="font-medium text-foreground">Delivery within 24 hours</p>
            <p className="text-sm text-muted-foreground">
              Most orders are completed in just a few hours
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3 text-left p-3 bg-secondary rounded-lg">
          <Mail className="w-5 h-5 text-primary flex-shrink-0" />
          <div>
            <p className="font-medium text-foreground">Check your email</p>
            <p className="text-sm text-muted-foreground">
              We&apos;ll send your video when it&apos;s ready
            </p>
          </div>
        </div>
      </div>

      <div className="space-y-3">
        <Button
          asChild
          className="w-full bg-primary hover:bg-primary/90"
        >
          <a href="https://nomadkaraoke.com">
            Back to Nomad Karaoke
          </a>
        </Button>

        <p className="text-xs text-muted-foreground">
          Questions? Email us at help@nomadkaraoke.com
        </p>
      </div>
    </div>
  )
}

export default function OrderSuccessPage() {
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
        <OrderSuccessContent />
      </Suspense>
    </div>
  )
}
