"use client"

import { useState } from "react"
import { useTranslations } from "next-intl"
import { api, BulkArtist, BulkAlbum, BulkTracklist } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Checkbox } from "@/components/ui/checkbox"
import { Badge } from "@/components/ui/badge"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { Loader2, Search, ChevronLeft } from "lucide-react"
import { AvailabilityBadge } from "./AvailabilityBadge"
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
  const [query, setQuery] = useState("")
  const [artists, setArtists] = useState<BulkArtist[]>([])
  const [artist, setArtist] = useState<BulkArtist | null>(null)
  const [albums, setAlbums] = useState<BulkAlbum[]>([])
  const [tracklist, setTracklist] = useState<BulkTracklist | null>(null)
  const [loading, setLoading] = useState<"" | "artists" | "albums" | "tracks">("")
  const [error, setError] = useState("")

  async function searchArtists() {
    if (query.trim().length < 2) return
    setLoading("artists"); setError(""); setArtists([]); setArtist(null); setAlbums([]); setTracklist(null)
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
    setArtist(a); setLoading("albums"); setError(""); setAlbums([]); setTracklist(null)
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
        is_extra: tr.is_extra,
        length_ms: tr.length_ms,
      })
    )
  }

  async function pickAlbum(album: BulkAlbum) {
    if (!artist?.name) return
    setLoading("tracks"); setError("")
    try {
      const tl = await api.getAlbumTracklist({
        artist: artist.name, releaseGroupMbid: album.release_group_mbid,
      })
      setTracklist(tl)
      onChange(buildRows(tl))
    } catch {
      setError(t("lookupFailed"))
    } finally {
      setLoading("")
    }
  }

  async function switchEdition(releaseMbid: string) {
    if (!artist?.name) return
    setLoading("tracks"); setError("")
    try {
      const tl = await api.getAlbumTracklist({ artist: artist.name, releaseMbid })
      setTracklist(tl)
      onChange(buildRows(tl))
    } catch {
      setError(t("lookupFailed"))
    } finally {
      setLoading("")
    }
  }

  const toggle = (key: string, checked: boolean) =>
    onChange(rows.map((r) => (r.key === key ? { ...r, selected: checked } : r)))

  // --- Render: tracklist view ---
  if (tracklist && artist) {
    const editionValue = tracklist.release_mbid || tracklist.canonical_release_mbid || ""
    return (
      <div className="space-y-3">
        <div className="flex items-center justify-between gap-2">
          <Button variant="ghost" size="sm" onClick={() => setTracklist(null)} disabled={disabled}>
            <ChevronLeft className="w-4 h-4 me-1" />{t("backToAlbums")}
          </Button>
          {tracklist.editions.length > 1 && (
            <Select value={editionValue} onValueChange={switchEdition} disabled={disabled}>
              <SelectTrigger className="h-8 w-[200px] text-xs">
                <SelectValue placeholder={t("edition")} />
              </SelectTrigger>
              <SelectContent>
                {tracklist.editions.map((e) => (
                  <SelectItem key={e.release_mbid} value={e.release_mbid}>
                    {(e.date || "?")} · {e.country || "—"} · {t("trackCount", { count: e.track_count })}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
        </div>

        <p className="text-sm font-medium" style={{ color: "var(--text)" }}>
          {artist.name} — {tracklist.title}
        </p>
        <p className="text-xs" style={{ color: "var(--text-muted)" }}>{t("tracklistHint")}</p>

        <div className="space-y-1 max-h-[420px] overflow-y-auto pe-1">
          {rows.map((row) => (
            <label
              key={row.key}
              className="flex items-center gap-2 rounded-md px-2 py-1.5 cursor-pointer hover:bg-white/5"
            >
              <Checkbox
                checked={row.selected}
                onCheckedChange={(v) => toggle(row.key, v === true)}
                disabled={disabled}
              />
              <span className="flex-1 min-w-0 text-sm truncate" style={{ color: "var(--text)" }}>
                {row.title}
              </span>
              {row.is_extra && (
                <Badge variant="outline" className="text-pink-400 border-pink-400/40">{t("extra")}</Badge>
              )}
              <AvailabilityBadge available={row.available} brands={row.brands} />
              <span className="text-xs w-10 text-end shrink-0" style={{ color: "var(--text-muted)" }}>
                {fmtLen(row.length_ms)}
              </span>
            </label>
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
