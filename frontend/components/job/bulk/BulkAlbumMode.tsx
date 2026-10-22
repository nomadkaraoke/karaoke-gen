"use client"

import { useRef, useState, type ReactNode } from "react"
import { useTranslations, useLocale } from "next-intl"
import { api, BulkArtist, BulkAlbum, BulkTracklist, BulkEditionVariant } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Checkbox } from "@/components/ui/checkbox"
import { Badge } from "@/components/ui/badge"
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select"
import { Loader2, Search, ChevronRight, Mic2, Disc3, ListMusic, Check, type LucideIcon } from "lucide-react"
import { CommunityVersions } from "./CommunityVersions"
import { BulkSongRow, newRow, shouldSelectTrack } from "./types"

interface BulkAlbumModeProps {
  rows: BulkSongRow[]
  onChange: (rows: BulkSongRow[]) => void
  /** Switch Bulk Mode to "By song name" (offered when no artist matches). */
  onSwitchToText?: () => void
  disabled?: boolean
}

function fmtLen(ms?: number | null): string {
  if (!ms) return ""
  const s = Math.round(ms / 1000)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`
}

type AlbumStep = "artist" | "album" | "tracks"

const STEPS: { key: AlbumStep; icon: LucideIcon; label: "stepArtist" | "stepAlbum" | "stepTracks" }[] = [
  { key: "artist", icon: Mic2, label: "stepArtist" },
  { key: "album", icon: Disc3, label: "stepAlbum" },
  { key: "tracks", icon: ListMusic, label: "stepTracks" },
]

/**
 * Artist → Album → Tracks progress strip. Completed steps show what was picked
 * and are clickable to jump back, so it doubles as the breadcrumb.
 */
function AlbumSteps({ current, artistName, albumTitle, onArtist, onAlbum, disabled }: {
  current: AlbumStep
  artistName?: string
  albumTitle?: string
  onArtist: () => void
  onAlbum: () => void
  disabled?: boolean
}) {
  const t = useTranslations("bulk")
  const currentIdx = STEPS.findIndex((s) => s.key === current)
  const picked: Record<AlbumStep, string | undefined> = { artist: artistName, album: albumTitle, tracks: undefined }
  const goBack: Partial<Record<AlbumStep, () => void>> = { artist: onArtist, album: onAlbum }

  return (
    <ol className="flex items-stretch gap-1" aria-label={t("stepsLabel")}>
      {STEPS.map((s, i) => {
        const done = i < currentIdx
        const active = i === currentIdx
        const Icon = done ? Check : s.icon
        const content = (
          <>
            <span
              className="w-7 h-7 rounded-full flex items-center justify-center shrink-0"
              style={
                active
                  ? { backgroundColor: "var(--brand-pink)", color: "#fff" }
                  : done
                    ? { backgroundColor: "rgba(255,122,204,0.15)", color: "var(--brand-pink)" }
                    : { backgroundColor: "rgba(255,255,255,0.06)", color: "var(--text-muted)" }
              }
            >
              <Icon className="w-3.5 h-3.5" />
            </span>
            <span className="min-w-0 text-start">
              <span
                className="block text-xs font-medium truncate"
                style={{ color: active || done ? "var(--text)" : "var(--text-muted)" }}
              >
                {i + 1}. {t(s.label)}
              </span>
              {done && picked[s.key] && (
                <span className="block text-[11px] truncate" style={{ color: "var(--text-muted)" }}>
                  {picked[s.key]}
                </span>
              )}
            </span>
          </>
        )
        return (
          <li key={s.key} className="flex-1 min-w-0 flex items-center gap-1">
            {done ? (
              <button
                type="button"
                onClick={goBack[s.key]}
                disabled={disabled}
                className="flex-1 min-w-0 flex items-center gap-2 rounded-md px-1.5 py-1 hover:bg-white/5"
              >
                {content}
              </button>
            ) : (
              <div
                className="flex-1 min-w-0 flex items-center gap-2 px-1.5 py-1"
                aria-current={active ? "step" : undefined}
              >
                {content}
              </div>
            )}
            {i < STEPS.length - 1 && (
              <ChevronRight className="w-4 h-4 shrink-0 rtl:rotate-180" style={{ color: "var(--text-muted)" }} />
            )}
          </li>
        )
      })}
    </ol>
  )
}

function StepHeading({ icon: Icon, title, subtitle }: { icon: LucideIcon; title: string; subtitle?: string }) {
  return (
    <div className="flex items-center gap-2">
      <Icon className="w-4 h-4 shrink-0" style={{ color: "var(--brand-pink)" }} />
      <div className="min-w-0">
        <p className="text-sm font-medium" style={{ color: "var(--text)" }}>{title}</p>
        {subtitle && <p className="text-xs truncate" style={{ color: "var(--text-muted)" }}>{subtitle}</p>}
      </div>
    </div>
  )
}

/** Cover Art Archive front-cover thumbnail for a release group (free, keyed by MBID). */
export function coverArtUrl(releaseGroupMbid: string, size: 250 | 500 = 250): string {
  return `https://coverartarchive.org/release-group/${encodeURIComponent(releaseGroupMbid)}/front-${size}`
}

/**
 * Album cover with the icon tile as placeholder: the image is only revealed once
 * it loads, and albums with no cover in the archive (404) keep the icon.
 */
function AlbumArt({ mbid, icon: Icon, className }: { mbid?: string | null; icon: LucideIcon; className: string }) {
  const [state, setState] = useState<"loading" | "loaded" | "failed">("loading")
  return (
    <span
      className={`${className} relative rounded-md overflow-hidden flex items-center justify-center shrink-0`}
      style={{ backgroundColor: "rgba(255,255,255,0.06)", color: "var(--text-muted)" }}
    >
      {state !== "loaded" && <Icon className="w-4 h-4" />}
      {mbid && state !== "failed" && (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={coverArtUrl(mbid)}
          alt=""
          loading="lazy"
          decoding="async"
          referrerPolicy="no-referrer"
          onLoad={() => setState("loaded")}
          onError={() => setState("failed")}
          className={`absolute inset-0 w-full h-full object-cover ${state === "loaded" ? "" : "invisible"}`}
        />
      )}
    </span>
  )
}

/** A bordered, obviously-clickable result row (artist or album) with a trailing chevron. */
function PickRow({ icon: Icon, artMbid, title, subtitle, meta, onClick, disabled }: {
  icon: LucideIcon
  /** Release-group MBID → show its cover art in place of the icon (album rows). */
  artMbid?: string
  title: string
  subtitle?: string
  meta?: ReactNode
  onClick: () => void
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="w-full text-start rounded-md border px-2.5 py-2 flex items-center gap-3 transition-colors hover:bg-white/5 hover:border-pink-400/50 disabled:opacity-50 disabled:pointer-events-none"
      style={{ borderColor: "var(--card-border)" }}
    >
      {artMbid ? (
        <AlbumArt mbid={artMbid} icon={Icon} className="w-10 h-10" />
      ) : (
        <span
          className="w-8 h-8 rounded-md flex items-center justify-center shrink-0"
          style={{ backgroundColor: "rgba(255,255,255,0.06)", color: "var(--text-muted)" }}
        >
          <Icon className="w-4 h-4" />
        </span>
      )}
      <span className="flex-1 min-w-0">
        <span className="block text-sm truncate" style={{ color: "var(--text)" }}>{title}</span>
        {subtitle && <span className="block text-xs truncate" style={{ color: "var(--text-muted)" }}>{subtitle}</span>}
      </span>
      {meta}
      <ChevronRight
        className="w-4 h-4 shrink-0 rtl:rotate-180"
        style={{ color: "var(--text-muted)" }}
      />
    </button>
  )
}

export function BulkAlbumMode({ rows, onChange, onSwitchToText, disabled }: BulkAlbumModeProps) {
  const t = useTranslations("bulk")
  const locale = useLocale()
  const [query, setQuery] = useState("")
  const [artists, setArtists] = useState<BulkArtist[]>([])
  const [artist, setArtist] = useState<BulkArtist | null>(null)
  const [albums, setAlbums] = useState<BulkAlbum[]>([])
  const [tracklist, setTracklist] = useState<BulkTracklist | null>(null)
  // Release group of the loaded album; edition switches pass it so the backend's
  // mirror lookup stays pruned to this album.
  const [albumMbid, setAlbumMbid] = useState("")
  // Release-group title of the loaded album: what trackers name the album (the
  // tracklist's title comes from a locale-picked pressing and may be translated).
  const [albumTitle, setAlbumTitle] = useState("")
  // Distinct tracklist variants for the loaded album. Held separately from
  // `tracklist` because switching to a specific edition returns no variant list.
  const [variants, setVariants] = useState<BulkEditionVariant[]>([])
  const [showVariants, setShowVariants] = useState(false)
  const [loading, setLoading] = useState<"" | "artists" | "albums" | "tracks">("")
  const [error, setError] = useState("")
  // The query whose artist search last finished without error (drives the no-results state).
  const [searchedQuery, setSearchedQuery] = useState("")
  // Bumped on every lookup and on step navigation; a response whose id is no
  // longer current is stale (user moved on) and is dropped.
  const requestId = useRef(0)

  function resetVariants() { setVariants([]); setShowVariants(false) }

  async function searchArtists() {
    if (query.trim().length < 2) return
    const id = ++requestId.current
    setLoading("artists"); setError(""); setSearchedQuery(""); setArtists([]); setArtist(null); setAlbums([]); setTracklist(null); resetVariants()
    try {
      const found = await api.searchAlbumArtists(query.trim())
      if (id === requestId.current) { setArtists(found); setSearchedQuery(query.trim()) }
    } catch {
      if (id === requestId.current) setError(t("lookupFailed"))
    } finally {
      if (id === requestId.current) setLoading("")
    }
  }

  async function pickArtist(a: BulkArtist) {
    if (!a.mbid) return
    const id = ++requestId.current
    setArtist(a); setLoading("albums"); setError(""); setAlbums([]); setTracklist(null); resetVariants()
    try {
      const found = await api.getAlbums(a.mbid)
      if (id === requestId.current) setAlbums(found)
    } catch {
      if (id === requestId.current) setError(t("lookupFailed"))
    } finally {
      if (id === requestId.current) setLoading("")
    }
  }

  function buildRows(tl: BulkTracklist, releaseGroupMbid: string, releaseGroupTitle: string): BulkSongRow[] {
    const album = {
      artist: artist?.name || "",
      title: releaseGroupTitle || tl.title || "",
      release_group_mbid: releaseGroupMbid || null,
      release_mbid: tl.selected_variant_mbid || tl.release_mbid || tl.canonical_release_mbid || null,
    }
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
        album: album.title ? album : undefined,
      })
    )
  }

  async function pickAlbum(album: BulkAlbum) {
    if (!artist?.name) return
    const id = ++requestId.current
    setLoading("tracks"); setError(""); resetVariants(); setAlbumMbid(album.release_group_mbid)
    setAlbumTitle(album.title)
    try {
      const tl = await api.getAlbumTracklist({
        artist: artist.name, releaseGroupMbid: album.release_group_mbid, locale,
      })
      if (id !== requestId.current) return
      setTracklist(tl)
      setVariants(tl.variants || [])
      onChange(buildRows(tl, album.release_group_mbid, album.title))
    } catch {
      if (id === requestId.current) setError(t("lookupFailed"))
    } finally {
      if (id === requestId.current) setLoading("")
    }
  }

  async function switchEdition(releaseMbid: string) {
    if (!artist?.name) return
    const id = ++requestId.current
    setLoading("tracks"); setError(""); setShowVariants(false)
    try {
      // Loading a specific variant returns no variant list — keep the existing one.
      const tl = await api.getAlbumTracklist({
        artist: artist.name, releaseMbid, releaseGroupMbid: albumMbid || undefined, locale,
      })
      if (id !== requestId.current) return
      setTracklist(tl)
      onChange(buildRows(tl, albumMbid, albumTitle))
    } catch {
      if (id === requestId.current) setError(t("lookupFailed"))
    } finally {
      if (id === requestId.current) setLoading("")
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

  // Navigating invalidates any in-flight lookup so it can't restore stale rows.
  const cancelPending = () => { requestId.current++; setLoading(""); setError("") }
  const goToArtists = () => { cancelPending(); setArtist(null); setAlbums([]); setTracklist(null); resetVariants(); onChange([]) }
  // Leaving the tracklist drops its rows so a stale album can't be submitted.
  const goToAlbums = () => { cancelPending(); setTracklist(null); resetVariants(); onChange([]) }

  const step: AlbumStep = tracklist && artist ? "tracks" : artist ? "album" : "artist"
  const stepper = (
    <AlbumSteps
      current={step}
      artistName={artist?.name}
      albumTitle={tracklist?.title ?? undefined}
      onArtist={goToArtists}
      onAlbum={goToAlbums}
      disabled={disabled}
    />
  )

  // --- Render: tracklist view ---
  if (tracklist && artist) {
    const currentMbid = tracklist.selected_variant_mbid || tracklist.release_mbid
      || tracklist.canonical_release_mbid || ""
    const selectedVariant = variants.find((v) => v.representative_release_mbid === currentMbid)
    const selectedCount = rows.filter((r) => r.selected).length
    const allSelected = rows.length > 0 && selectedCount === rows.length
    return (
      <div className="space-y-3">
        {stepper}
        <div className="flex items-center gap-3">
          {albumMbid && <AlbumArt key={albumMbid} mbid={albumMbid} icon={Disc3} className="w-12 h-12" />}
          <StepHeading icon={ListMusic} title={t("chooseTracks")} subtitle={`${artist.name} — ${tracklist.title}`} />
        </div>

        <div className="flex items-center justify-between gap-2 flex-wrap">
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              className="h-7 text-xs"
              onClick={() => onChange(rows.map((r) => ({ ...r, selected: !allSelected })))}
              disabled={disabled || rows.length === 0}
            >
              {allSelected ? t("selectNone") : t("selectAll")}
            </Button>
            <span className="text-xs tabular-nums" style={{ color: "var(--text-muted)" }}>
              {t("tracksSelected", { selected: selectedCount, total: rows.length })}
            </span>
          </div>
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
        {error && <p className="text-xs" style={{ color: "#ef4444" }}>{error}</p>}
      </div>
    )
  }

  // --- Render: album list view ---
  if (artist) {
    return (
      <div className="space-y-3">
        {stepper}
        <StepHeading icon={Disc3} title={t("chooseAlbum")} subtitle={t("albumsBy", { artist: artist.name })} />
        {loading === "albums" || loading === "tracks" ? (
          <Loader2 className="w-5 h-5 animate-spin" style={{ color: "var(--text-muted)" }} />
        ) : (
          <div className="space-y-1.5 max-h-[420px] overflow-y-auto pe-1">
            {albums.map((a) => (
              <PickRow
                key={a.release_group_mbid}
                icon={Disc3}
                artMbid={a.release_group_mbid}
                onClick={() => pickAlbum(a)}
                disabled={disabled}
                title={a.title}
                meta={
                  <>
                    {!a.is_studio && a.secondary_types[0] && (
                      <Badge variant="outline" className="text-xs">{a.secondary_types[0]}</Badge>
                    )}
                    <span className="text-xs shrink-0 tabular-nums" style={{ color: "var(--text-muted)" }}>
                      {a.first_release_date?.slice(0, 4)}
                    </span>
                  </>
                }
              />
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
      {stepper}
      <StepHeading icon={Mic2} title={t("chooseArtist")} subtitle={t("chooseArtistHint")} />
      <div className="flex items-center gap-2">
        <Input
          placeholder={t("artistSearchPlaceholder")}
          value={query}
          disabled={disabled}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && searchArtists()}
        />
        <Button onClick={searchArtists} disabled={disabled || query.trim().length < 2}>
          {loading === "artists"
            ? <Loader2 className="w-4 h-4 me-1 animate-spin" />
            : <Search className="w-4 h-4 me-1" />}
          {t("searchArtists")}
        </Button>
      </div>
      <div className="space-y-1.5 max-h-[420px] overflow-y-auto pe-1">
        {artists.map((a) => (
          <PickRow
            key={a.mbid || a.name}
            icon={Mic2}
            onClick={() => pickArtist(a)}
            disabled={disabled || !a.mbid}
            title={a.name}
            subtitle={[a.alias && t("artistAlsoKnownAs", { alias: a.alias }), a.disambiguation].filter(Boolean).join(" · ")}
          />
        ))}
      </div>
      {searchedQuery && artists.length === 0 && !loading && (
        <div
          className="rounded-md border border-dashed px-3 py-3 space-y-2"
          style={{ borderColor: "var(--card-border)" }}
          data-testid="bulk-artist-no-results"
        >
          <p className="text-sm" style={{ color: "var(--text)" }}>{t("noArtistsFound", { query: searchedQuery })}</p>
          <p className="text-xs" style={{ color: "var(--text-muted)" }}>{t("noArtistsFoundHint")}</p>
          {onSwitchToText && (
            <Button variant="outline" size="sm" onClick={onSwitchToText} disabled={disabled}>
              {t("noArtistsUseText")}
            </Button>
          )}
        </div>
      )}
      {error && <p className="text-xs" style={{ color: "#ef4444" }}>{error}</p>}
    </div>
  )
}
