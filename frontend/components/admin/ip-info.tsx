"use client"

import { useEffect, useState } from "react"
import { fetchIpInfo, countryCodeToFlag } from "@/lib/ip-geolocation"
import type { IpGeoInfo } from "@/lib/api"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import { Globe } from "lucide-react"

/**
 * Inline IP address display with geolocation enrichment.
 *
 * Shows the raw IP plus a compact summary (flag + country + ISP).
 * Hover to see full details (city, region, ASN, timezone).
 *
 * Usage: <IpInfo ip="8.8.8.8" />
 */
export function IpInfo({
  ip,
  compact = false,
}: {
  ip: string | null | undefined
  compact?: boolean
}) {
  const [geo, setGeo] = useState<IpGeoInfo | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!ip) return
    setLoading(true)
    fetchIpInfo(ip).then((result) => {
      setGeo(result)
      setLoading(false)
    })
  }, [ip])

  if (!ip) {
    return <span className="text-muted-foreground text-xs">-</span>
  }

  const hasGeo = geo && geo.status === "success"
  const flag = hasGeo ? countryCodeToFlag(geo.country_code || "") : ""
  const ispShort = hasGeo && geo.isp ? truncateIsp(geo.isp) : ""

  // Inline summary
  const summary = hasGeo ? (
    <span className="text-muted-foreground text-xs ml-1">
      {flag} {geo.country_code}
      {ispShort && <span className="hidden sm:inline"> · {ispShort}</span>}
    </span>
  ) : loading ? (
    <span className="text-muted-foreground text-xs ml-1 animate-pulse">...</span>
  ) : null

  if (compact) {
    // Compact mode: just flag + country code, no IP shown
    if (!hasGeo) return <span className="font-mono text-xs">{ip}</span>
    return (
      <HoverCard openDelay={200}>
        <HoverCardTrigger asChild>
          <span className="cursor-help font-mono text-xs">
            {ip} {summary}
          </span>
        </HoverCardTrigger>
        <HoverCardContent className="w-72" side="top">
          <IpGeoDetails ip={ip} geo={geo} />
        </HoverCardContent>
      </HoverCard>
    )
  }

  return (
    <HoverCard openDelay={200}>
      <HoverCardTrigger asChild>
        <span className="cursor-help inline-flex items-center gap-0.5">
          <code className="text-xs bg-muted px-1.5 py-0.5 rounded font-mono">{ip}</code>
          {summary}
        </span>
      </HoverCardTrigger>
      <HoverCardContent className="w-72" side="top">
        <IpGeoDetails ip={ip} geo={geo} />
      </HoverCardContent>
    </HoverCard>
  )
}

function IpGeoDetails({
  ip,
  geo,
}: {
  ip: string
  geo: IpGeoInfo | null
}) {
  if (!geo || geo.status !== "success") {
    return (
      <div className="text-sm">
        <div className="flex items-center gap-2 mb-2">
          <Globe className="w-4 h-4 text-muted-foreground" />
          <span className="font-medium">{ip}</span>
        </div>
        <p className="text-xs text-muted-foreground">
          {geo?.status === "private" ? "Private/reserved IP" :
           geo?.status === "error" ? "Lookup failed" :
           "No geolocation data available"}
        </p>
      </div>
    )
  }

  const flag = countryCodeToFlag(geo.country_code || "")

  return (
    <div className="text-sm space-y-1.5">
      <div className="flex items-center gap-2 mb-2">
        <Globe className="w-4 h-4 text-muted-foreground" />
        <span className="font-medium font-mono">{ip}</span>
      </div>
      <DetailRow label="Country" value={`${flag} ${geo.country} (${geo.country_code})`} />
      {geo.region && <DetailRow label="Region" value={geo.region} />}
      {geo.city && <DetailRow label="City" value={geo.city} />}
      {geo.isp && <DetailRow label="ISP" value={geo.isp} />}
      {geo.org && geo.org !== geo.isp && <DetailRow label="Org" value={geo.org} />}
      {geo.as_number && (
        <DetailRow label="ASN" value={`${geo.as_number}${geo.as_name ? ` (${geo.as_name})` : ""}`} />
      )}
      {geo.timezone && <DetailRow label="Timezone" value={geo.timezone} />}
    </div>
  )
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-2">
      <span className="text-muted-foreground text-xs">{label}</span>
      <span className="text-xs text-right">{value}</span>
    </div>
  )
}

function truncateIsp(isp: string): string {
  // Remove common suffixes to keep it short
  const cleaned = isp
    .replace(/,?\s*(Inc|LLC|Ltd|Corp|Corporation|Co\.|Company|Telecommunications|Telecom)\.?$/i, "")
    .trim()
  return cleaned.length > 20 ? cleaned.slice(0, 20) + "..." : cleaned
}
