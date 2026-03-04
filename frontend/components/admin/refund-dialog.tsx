"use client"

import { useState } from "react"
import { adminApi, PaymentRecord } from "@/lib/api"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { AlertTriangle, Loader2 } from "lucide-react"
import { useToast } from "@/hooks/use-toast"

function formatCents(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`
}

interface RefundDialogProps {
  payment: PaymentRecord | null
  onClose: () => void
  onSuccess: () => void
}

export function RefundDialog({ payment, onClose, onSuccess }: RefundDialogProps) {
  const [isPartial, setIsPartial] = useState(false)
  const [amount, setAmount] = useState("")
  const [reason, setReason] = useState("requested_by_customer")
  const [processing, setProcessing] = useState(false)
  const { toast } = useToast()

  if (!payment) return null

  const maxRefundable = payment.amount_total - (payment.refund_amount || 0)
  const refundAmount = isPartial ? Math.round(parseFloat(amount || "0") * 100) : maxRefundable
  const isValid = refundAmount > 0 && refundAmount <= maxRefundable

  const creditsAtRisk = payment.order_type === "credit_purchase" && payment.credits_granted > 0
    ? Math.floor(payment.credits_granted * (refundAmount / payment.amount_total))
    : 0

  const handleRefund = async () => {
    if (!isValid) return
    setProcessing(true)
    try {
      const result = await adminApi.refundPayment(payment.session_id, {
        amount: isPartial ? refundAmount : undefined,
        reason,
      })
      toast({
        title: "Refund processed",
        description: result.message,
      })
      onSuccess()
    } catch (err) {
      const message = err instanceof Error ? err.message : "Refund failed"
      toast({
        title: "Refund failed",
        description: message,
        variant: "destructive",
      })
    } finally {
      setProcessing(false)
    }
  }

  return (
    <AlertDialog open={!!payment} onOpenChange={(open) => !open && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Issue Refund</AlertDialogTitle>
          <AlertDialogDescription>
            Refund for {payment.customer_email} - {payment.product_description}
          </AlertDialogDescription>
        </AlertDialogHeader>

        <div className="space-y-4 py-4">
          {/* Payment Summary */}
          <div className="rounded-lg border p-3 space-y-1">
            <div className="flex justify-between text-sm">
              <span className="text-muted-foreground">Original amount</span>
              <span className="font-medium">{formatCents(payment.amount_total)}</span>
            </div>
            {payment.refund_amount > 0 && (
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">Already refunded</span>
                <span className="text-destructive">{formatCents(payment.refund_amount)}</span>
              </div>
            )}
            <div className="flex justify-between text-sm font-medium">
              <span>Max refundable</span>
              <span>{formatCents(maxRefundable)}</span>
            </div>
          </div>

          {/* Partial toggle */}
          <div className="flex items-center gap-2">
            <Switch
              id="partial-refund"
              checked={isPartial}
              onCheckedChange={setIsPartial}
            />
            <Label htmlFor="partial-refund">Partial refund</Label>
          </div>

          {/* Amount input */}
          {isPartial && (
            <div className="space-y-2">
              <Label>Refund amount ($)</Label>
              <Input
                type="number"
                step="0.01"
                min="0.01"
                max={(maxRefundable / 100).toFixed(2)}
                placeholder={(maxRefundable / 100).toFixed(2)}
                value={amount}
                onChange={(e) => setAmount(e.target.value)}
              />
            </div>
          )}

          {/* Reason */}
          <div className="space-y-2">
            <Label>Reason</Label>
            <Select value={reason} onValueChange={setReason}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="requested_by_customer">Requested by customer</SelectItem>
                <SelectItem value="duplicate">Duplicate payment</SelectItem>
                <SelectItem value="fraudulent">Fraudulent</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {/* Credit deduction warning */}
          {creditsAtRisk > 0 && (
            <div className="rounded-lg border border-yellow-500/50 bg-yellow-500/10 p-3 flex gap-2">
              <AlertTriangle className="h-4 w-4 text-yellow-500 mt-0.5 shrink-0" />
              <div className="text-sm">
                <span className="font-medium">Credit deduction:</span>{" "}
                {creditsAtRisk} credit{creditsAtRisk !== 1 ? "s" : ""} will be
                deducted from {payment.customer_email}
              </div>
            </div>
          )}

          {/* Refund summary */}
          {isValid && (
            <div className="rounded-lg bg-muted p-3 text-center">
              <span className="text-lg font-bold text-destructive">
                Refund {formatCents(refundAmount)}
              </span>
            </div>
          )}
        </div>

        <AlertDialogFooter>
          <AlertDialogCancel disabled={processing}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={handleRefund}
            disabled={!isValid || processing}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
          >
            {processing && <Loader2 className="w-4 h-4 mr-2 animate-spin" />}
            Process Refund
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
