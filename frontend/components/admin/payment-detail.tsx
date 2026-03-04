"use client"

import { useEffect, useState } from "react"
import { adminApi, PaymentRecord } from "@/lib/api"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import { Skeleton } from "@/components/ui/skeleton"
import { ExternalLink, Receipt, Undo2, Loader2 } from "lucide-react"
import Link from "next/link"

function formatCents(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`
}

function formatDate(dateStr?: string | null): string {
  if (!dateStr) return "-"
  return new Date(dateStr).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  })
}

function StatusBadge({ status }: { status: string }) {
  const variants: Record<string, { variant: "default" | "secondary" | "destructive" | "outline"; label: string }> = {
    succeeded: { variant: "default", label: "Succeeded" },
    refunded: { variant: "destructive", label: "Refunded" },
    partially_refunded: { variant: "secondary", label: "Partial Refund" },
    disputed: { variant: "destructive", label: "Disputed" },
  }
  const v = variants[status] || { variant: "outline" as const, label: status }
  return <Badge variant={v.variant}>{v.label}</Badge>
}

function DetailRow({ label, value, mono }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div className="flex justify-between items-start py-1">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className={`text-sm text-right max-w-[200px] truncate ${mono ? "font-mono text-xs" : ""}`}>
        {value || "-"}
      </span>
    </div>
  )
}

interface PaymentDetailSheetProps {
  payment: PaymentRecord | null
  onClose: () => void
  onRefund: (payment: PaymentRecord) => void
}

export function PaymentDetailSheet({ payment, onClose, onRefund }: PaymentDetailSheetProps) {
  const [detail, setDetail] = useState<PaymentRecord | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!payment) {
      setDetail(null)
      return
    }
    setLoading(true)
    adminApi.getPaymentDetail(payment.session_id)
      .then(setDetail)
      .catch(() => setDetail(payment))
      .finally(() => setLoading(false))
  }, [payment])

  const data = detail || payment
  const canRefund = data && data.status === "succeeded" && data.refund_amount < data.amount_total

  return (
    <Sheet open={!!payment} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="overflow-y-auto p-6">
        <SheetHeader>
          <SheetTitle>Payment Details</SheetTitle>
          <SheetDescription>
            {data?.product_description || "Payment"}
          </SheetDescription>
        </SheetHeader>

        {loading ? (
          <div className="space-y-4 mt-6">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-6 w-full" />
            ))}
          </div>
        ) : data ? (
          <div className="space-y-6 mt-6">
            {/* Status & Amount */}
            <div className="text-center space-y-2">
              <div className="text-3xl font-bold">{formatCents(data.amount_total)}</div>
              <StatusBadge status={data.status} />
              {data.refund_amount > 0 && (
                <div className="text-sm text-destructive">
                  Refunded: {formatCents(data.refund_amount)}
                </div>
              )}
            </div>

            <Separator />

            {/* Customer */}
            <div>
              <h4 className="text-sm font-medium mb-2">Customer</h4>
              <DetailRow label="Email" value={
                <Link
                  href={`/admin/users/detail?email=${encodeURIComponent(data.customer_email)}`}
                  className="text-primary hover:underline"
                >
                  {data.customer_email}
                </Link>
              } />
              <DetailRow label="Name" value={data.customer_name} />
            </div>

            <Separator />

            {/* Financial */}
            <div>
              <h4 className="text-sm font-medium mb-2">Financial</h4>
              <DetailRow label="Gross" value={formatCents(data.amount_total)} />
              <DetailRow label="Stripe Fee" value={formatCents(data.stripe_fee)} />
              <DetailRow label="Net" value={formatCents(data.net_amount)} />
              {data.discount_amount > 0 && (
                <DetailRow label="Discount" value={formatCents(data.discount_amount)} />
              )}
            </div>

            <Separator />

            {/* Payment Method */}
            <div>
              <h4 className="text-sm font-medium mb-2">Payment Method</h4>
              <DetailRow label="Type" value={
                <span className="capitalize">{data.payment_method_type?.replace("_", " ") || "-"}</span>
              } />
              {data.card_brand && <DetailRow label="Card" value={`${data.card_brand} ...${data.card_last4}`} />}
            </div>

            <Separator />

            {/* Order Details */}
            <div>
              <h4 className="text-sm font-medium mb-2">Order</h4>
              <DetailRow label="Type" value={
                data.order_type === "made_for_you" ? "Made For You" : "Credit Purchase"
              } />
              <DetailRow label="Product" value={data.product_description} />
              {data.credits_granted > 0 && (
                <DetailRow label="Credits" value={data.credits_granted} />
              )}
              {data.artist && <DetailRow label="Song" value={`${data.artist} - ${data.title}`} />}
              {data.job_id && <DetailRow label="Job ID" value={data.job_id} mono />}
            </div>

            <Separator />

            {/* Timestamps */}
            <div>
              <h4 className="text-sm font-medium mb-2">Timestamps</h4>
              <DetailRow label="Created" value={formatDate(data.created_at)} />
              <DetailRow label="Processed" value={formatDate(data.processed_at)} />
              {data.refunded_at && (
                <DetailRow label="Refunded" value={formatDate(data.refunded_at)} />
              )}
            </div>

            <Separator />

            {/* IDs */}
            <div>
              <h4 className="text-sm font-medium mb-2">Stripe IDs</h4>
              <DetailRow label="Session" value={data.session_id} mono />
              <DetailRow label="Payment Intent" value={data.payment_intent_id} mono />
              <DetailRow label="Charge" value={data.charge_id} mono />
            </div>

            {/* Actions */}
            <div className="flex flex-col gap-2 pt-2">
              {data.stripe_dashboard_url && (
                <Button variant="outline" size="sm" asChild>
                  <a href={data.stripe_dashboard_url} target="_blank" rel="noopener noreferrer">
                    <ExternalLink className="w-4 h-4 mr-2" />
                    View in Stripe
                  </a>
                </Button>
              )}
              {data.receipt_url && (
                <Button variant="outline" size="sm" asChild>
                  <a href={data.receipt_url} target="_blank" rel="noopener noreferrer">
                    <Receipt className="w-4 h-4 mr-2" />
                    View Receipt
                  </a>
                </Button>
              )}
              {canRefund && (
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => onRefund(data)}
                  title="Issue a refund for this payment"
                >
                  <Undo2 className="w-4 h-4 mr-2" />
                  Issue Refund
                </Button>
              )}
            </div>
          </div>
        ) : null}
      </SheetContent>
    </Sheet>
  )
}
