"use client"

import { useTranslations } from "next-intl"
import { Youtube } from "lucide-react"
import type { CommunityVersion } from "@/lib/api"

interface CommunityVersionsProps {
  available?: boolean | null
  versions?: CommunityVersion[]
}

/**
 * Existing community karaoke versions for a track, shown as a vertical list of
 * clickable YouTube links so the user can preview each and decide whether to
 * remake it with our generator. Falls back to a plain "exists" line when we know
 * a version exists but have no playable URL. Renders nothing if the track isn't
 * already available.
 */
export function CommunityVersions({ available, versions }: CommunityVersionsProps) {
  const t = useTranslations("bulk")
  if (!available) return null
  const list = versions || []

  if (list.length === 0) {
    return <p className="text-xs text-amber-500/90 ps-7">{t("communityExists")}</p>
  }

  return (
    <div className="ps-7 mt-0.5 space-y-0.5">
      <p className="text-xs" style={{ color: "var(--text-muted)" }}>{t("existingVersions")}</p>
      <ul className="space-y-0.5">
        {list.map((v) => {
          // Only ever render http(s) hrefs — never an attacker-controlled scheme.
          const safe = /^https?:\/\//i.test(v.url)
          return (
            <li key={`${v.brand}-${v.url}`}>
              {safe ? (
                <a
                  href={v.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 text-xs text-amber-500 hover:underline"
                >
                  <Youtube className="w-3.5 h-3.5 shrink-0" />
                  <span>{v.brand}</span>
                </a>
              ) : (
                <span className="inline-flex items-center gap-1.5 text-xs text-amber-500/80">
                  <Youtube className="w-3.5 h-3.5 shrink-0" />
                  <span>{v.brand}</span>
                </span>
              )}
            </li>
          )
        })}
      </ul>
    </div>
  )
}
