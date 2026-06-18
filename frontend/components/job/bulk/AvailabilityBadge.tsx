"use client"

import { useTranslations } from "next-intl"
import { Badge } from "@/components/ui/badge"
import { Check } from "lucide-react"

interface AvailabilityBadgeProps {
  available?: boolean | null
  brands?: string[]
}

/** Shows "community version exists" when a track is already on KaraokeNerds. */
export function AvailabilityBadge({ available, brands }: AvailabilityBadgeProps) {
  const t = useTranslations("bulk")
  if (!available) return null
  const brandList = (brands || []).slice(0, 3).join(", ")
  return (
    <Badge variant="outline" className="text-amber-500 border-amber-500/40 gap-1">
      <Check className="w-3 h-3" />
      {brandList ? t("communityExistsWithBrands", { brands: brandList }) : t("communityExists")}
    </Badge>
  )
}
