"use client"

import { useState } from "react"
import { useTranslations, useLocale } from "next-intl"
import { api, BulkArtist, BulkAlbum, BulkTracklist, BulkEditionVariant } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Checkbox } from "@/components/ui/checkbox"
import { Badge } from "@/components/ui/badge"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { Loader2, Search, ChevronLeft } from "lucide-react"
import { CommunityVersions } from "./CommunityVersions"
import { BulkSongRow, newRow, shouldSelectTrack } from "./types"

interface BulkAlbumModeProps {
  rows: BulkSongRow[]
  onChange: (rows: BulkSongRow[]) => void
  disabled?: boolean
}

function fmtLen(ms?: number | null): string {
  if (!ms) return ""
  const s = Math.round(ms / 1000)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`
}

export function BulkAlbumMode({ rows, onChange, disabled }: BulkAlbumModeProps) {
  const t = useTranslations("bulk")
  const locale = useLocale()
  const [query, setQuery] = useState("")
  const [artists, setArtists] = useState<BulkArtist[]>([])
  const [artist, setArtist] = useState<BulkArtist | null>(null)
  const [albums, setAlbums] = useState<BulkAlbum[]>([])
  const [tracklist, setTracklist] = useState<BulkTracklist | null>(null)
  // Distinct tracklist variants for the loaded album. Held separately from
  // `tracklist` because switching to a specific edition returns no variant list.
  const [variants, setVariants] = useState<BulkEditionVariant[]>([])
  const [showVariants, setShowVariants] = useState(false)
  const [loading, setLoading] = useState<"" | "artists" | "albums" | "tracks">("")
  const [error, setError] = useState("")

  function resetVariants() { setVariants([]); setShowVariants(false) }

  async function searchArtists() {
    if (query.trim().length < 2) return
    setLoading("artists"); setError(""); setArtists([]); setArtist(null); setAlbums([]); setTracklist(null); resetVariants()
    try {
      setArtists(await api.searchAlbumArtists(query.trim()))
    } catch {
      setError(t("lookupFailed"))
    } finally {
      setLoading("")
    }
  }

  async function pickArtist(a: BulkArtist) {
    if (!a.mbid) return
    setArtist(a); setLoading("albums"); setError(""); setAlbums([]); setTracklist(null); resetVariants()
    try {
      setAlbums(await api.getAlbums(a.mbid))
    } catch {
      setError(t("lookupFailed"))
    } finally {
      setLoading("")
    }
  }

  function buildRows(tl: BulkTracklist): BulkSongRow[] {
    return tl.tracks.map((tr) =>
      newRow({
        artist: artist?.name || "",
        title: tr.title,
        // Default-uncheck extras and tracks that already have a community version.
        selected: shouldSelectTrack(tr),
        available: tr.available,
        brands: tr.brands,
        versions: tr.versions,
        is_extra: tr.is_extra,
        length_ms: tr.length_ms,
        position: tr.position,
      })
    )
  }

  async function pickAlbum(album: BulkAlbum) {
    if (!artist?.name) return
    setLoading("tracks"); setError(""); resetVariants()
    try {
      const tl = await api.getAlbumTracklist({
        artist: artist.name, releaseGroupMbid: album.release_group_mbid, locale,
      })
      setTracklist(tl)
      setVariants(tl.variants || [])
      onChange(buildRows(tl))
    } catch {
      setError(t("lookupFailed"))
    } finally {
      setLoading("")
    }
  }

  async function switchEdition(releaseMbid: string) {
    if (!artist?.name) return
    setLoading("tracks"); setError(""); setShowVariants(false)
    try {
      // Loading a specific variant returns no variant list — keep the existing one.
      const tl = await api.getAlbumTracklist({ artist: artist.name, releaseMbid, locale })
      setTracklist(tl)
      onChange(buildRows(tl))
    } catch {
      setError(t("lookupFailed"))
    } finally {
      setLoading("")
    }
  }

  // Translate the backend's English variant label; compose the "+N bonus" suffix.
  const variantLabel = (label: string) =>
    label === "Original" ? t("variantOriginal") : t("variantReissue")
  const variantBonus = (delta: number) => (delta > 0 ? ` (${t("variantBonus", { count: delta })})` : "")
  const variantText = (v: BulkEditionVariant) =>
    `${variantLabel(v.label)} · ${v.year || "?"} · ${t("trackCount", { count: v.track_count })}${variantBonus(v.delta_vs_original)}`

  const toggle = (key: string, checked: boolean) =>
    onChange(rows.map((r) => (r.key === key ? { ...r, selected: checked } : r)))

  // --- Render: tracklist view ---
  if (tracklist && artist) {
    const currentMbid = tracklist.selected_variant_mbid || tracklist.release_mbid
      || tracklist.canonical_release_mbid || ""
    const selectedVariant = variants.find((v) => v.representative_release_mbid === currentMbid)
    return (
      <div className="space-y-3">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => { setTracklist(null); resetVariants() }}
            disabled={disabled}
          >
            <ChevronLeft className="w-4 h-4 me-1" />{t("backToAlbums")}
          </Button>
          {/* Single confident default; full variant list only on "Change". */}
          {variants.length > 0 && selectedVariant && (
            showVariants && variants.length > 1 ? (
              <Select value={currentMbid} onValueChange={switchEdition} disabled={disabled}>
                <SelectTrigger className="h-8 w-[260px] text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {variants.map((v) => (
                    <SelectItem key={v.representative_release_mbid} value={v.representative_release_mbid}>
                      {variantText(v)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            ) : (
              <div className="flex items-center gap-2">
                <span className="text-xs" style={{ color: "var(--text-muted)" }}>
                  {t("usingRelease", {
                    label: variantLabel(selectedVariant.label),
                    year: selectedVariant.year || "?",
                    tracks: t("trackCount", { count: selectedVariant.track_count }),
                  })}
                  {variantBonus(selectedVariant.delta_vs_original)}
                </span>
                {variants.length > 1 && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 text-xs"
                    onClick={() => setShowVariants(true)}
                    disabled={disabled}
                  >
                    {t("changeEdition")}
                  </Button>
                )}
              </div>
            )
          )}
        </div>

        <p className="text-sm font-medium" style={{ color: "var(--text)" }}>
          {artist.name} — {tracklist.title}
        </p>
        <p className="text-xs" style={{ color: "var(--text-muted)" }}>{t("tracklistHint")}</p>

        <div className="space-y-1 max-h-[420px] overflow-y-auto pe-1">
          {rows.map((row) => (
            <div key={row.key} className="rounded-md px-2 py-1.5 hover:bg-white/5">
              <label className="flex items-center gap-2 cursor-pointer">
                <Checkbox
                  checked={row.selected}
                  onCheckedChange={(v) => toggle(row.key, v === true)}
                  disabled={disabled}
                />
                {row.position != null && (
                  <span
                    className="text-xs w-5 text-end shrink-0 tabular-nums"
                    style={{ color: "var(--text-muted)" }}
                  >
                    {row.position}
                  </span>
                )}
                <span className="flex-1 min-w-0 text-sm truncate" style={{ color: "var(--text)" }}>
                  {row.title}
                </span>
                {row.is_extra && (
                  <Badge variant="outline" className="text-pink-400 border-pink-400/40">{t("extra")}</Badge>
                )}
                <span className="text-xs w-10 text-end shrink-0" style={{ color: "var(--text-muted)" }}>
                  {fmtLen(row.length_ms)}
                </span>
              </label>
              {/* Outside the <label> so clicking a version link never toggles the checkbox. */}
              <CommunityVersions available={row.available} versions={row.versions} />
            </div>
          ))}
        </div>
      </div>
    )
  }

  // --- Render: album list view ---
  if (artist) {
    return (
      <div className="space-y-3">
        <Button variant="ghost" size="sm" onClick={() => { setArtist(null); setAlbums([]) }} disabled={disabled}>
          <ChevronLeft className="w-4 h-4 me-1" />{t("backToArtists")}
        </Button>
        <p className="text-sm font-medium" style={{ color: "var(--text)" }}>{artist.name}</p>
        {loading === "albums" ? (
          <Loader2 className="w-5 h-5 animate-spin" style={{ color: "var(--text-muted)" }} />
        ) : (
          <div className="space-y-1 max-h-[420px] overflow-y-auto pe-1">
            {albums.map((a) => (
              <button
                key={a.release_group_mbid}
                onClick={() => pickAlbum(a)}
                disabled={disabled}
                className="w-full text-start rounded-md px-2 py-1.5 hover:bg-white/5 flex items-center gap-2"
              >
                <span className="flex-1 min-w-0 text-sm truncate" style={{ color: "var(--text)" }}>{a.title}</span>
                {!a.is_studio && a.secondary_types[0] && (
                  <Badge variant="outline" className="text-xs">{a.secondary_types[0]}</Badge>
                )}
                <span className="text-xs shrink-0" style={{ color: "var(--text-muted)" }}>
                  {a.first_release_date?.slice(0, 4)}
                </span>
              </button>
            ))}
            {albums.length === 0 && <p className="text-xs" style={{ color: "var(--text-muted)" }}>{t("noAlbums")}</p>}
          </div>
        )}
        {error && <p className="text-xs" style={{ color: "#ef4444" }}>{error}</p>}
      </div>
    )
  }

  // --- Render: artist search view ---
  return (
    <div className="space-y-3">
      <p className="text-xs" style={{ color: "var(--text-muted)" }}>{t("albumModeHint")}</p>
      <div className="flex items-center gap-2">
        <Input
          placeholder={t("artistSearchPlaceholder")}
          value={query}
          disabled={disabled}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && searchArtists()}
        />
        <Button onClick={searchArtists} disabled={disabled || query.trim().length < 2}>
          {loading === "artists" ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
        </Button>
      </div>
      <div className="space-y-1 max-h-[420px] overflow-y-auto pe-1">
        {artists.map((a) => (
          <button
            key={a.mbid || a.name}
            onClick={() => pickArtist(a)}
            disabled={disabled || !a.mbid}
            className="w-full text-start rounded-md px-2 py-1.5 hover:bg-white/5 flex items-center gap-2"
          >
            <span className="text-sm" style={{ color: "var(--text)" }}>{a.name}</span>
            {a.disambiguation && (
              <span className="text-xs" style={{ color: "var(--text-muted)" }}>({a.disambiguation})</span>
            )}
          </button>
        ))}
      </div>
      {error && <p className="text-xs" style={{ color: "#ef4444" }}>{error}</p>}
    </div>
  )
}
