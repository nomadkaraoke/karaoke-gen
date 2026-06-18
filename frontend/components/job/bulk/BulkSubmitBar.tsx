"use client"

import { useTranslations } from "next-intl"
import { Button } from "@/components/ui/button"
import { Loader2 } from "lucide-react"
import { canSubmitBatch } from "./types"

interface BulkSubmitBarProps {
  selectedCount: number
  credits: number | null
  isAdmin: boolean
  isSubmitting: boolean
  onSubmit: () => void
  onBuyCredits?: () => void
  maxSongs: number
}

export function BulkSubmitBar({
  selectedCount, credits, isAdmin, isSubmitting, onSubmit, onBuyCredits, maxSongs,
}: BulkSubmitBarProps) {
  const t = useTranslations("bulk")
  const overLimit = selectedCount > maxSongs
  // Gate: 1 credit/song minimum. Admins bypass.
  const enoughCredits = isAdmin || credits === null || credits >= selectedCount
  const canSubmit = canSubmitBatch(selectedCount, credits, isAdmin, maxSongs) && !isSubmitting

  return (
    <div className="rounded-lg border p-3 space-y-2" style={{ borderColor: "var(--card-border)" }}>
      <div className="flex items-center justify-between text-sm" style={{ color: "var(--text)" }}>
        <span>{t("selectedCount", { count: selectedCount })}</span>
        {!isAdmin && credits !== null && (
          <span style={{ color: enoughCredits ? "var(--text-muted)" : "#ef4444" }}>
            {t("creditsCost", { cost: selectedCount, balance: credits })}
          </span>
        )}
      </div>

      {overLimit && (
        <p className="text-xs" style={{ color: "#ef4444" }}>{t("overLimit", { max: maxSongs })}</p>
      )}

      {!enoughCredits && !overLimit && (
        <div className="flex items-center justify-between gap-2">
          <p className="text-xs" style={{ color: "#ef4444" }}>
            {t("notEnoughCredits", { needed: selectedCount, have: credits ?? 0 })}
          </p>
          {onBuyCredits && (
            <Button size="sm" variant="outline" onClick={onBuyCredits}>{t("buyCredits")}</Button>
          )}
        </div>
      )}

      <Button className="w-full" onClick={onSubmit} disabled={!canSubmit}>
        {isSubmitting ? (
          <><Loader2 className="w-4 h-4 me-2 animate-spin" />{t("submitting")}</>
        ) : (
          t("createBatch", { count: selectedCount })
        )}
      </Button>
    </div>
  )
}
