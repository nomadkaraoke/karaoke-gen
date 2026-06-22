"use client"

import { useEffect, useRef, useState } from "react"
import { useTranslations } from "next-intl"
import { api } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Plus, X } from "lucide-react"
import { CommunityVersions } from "./CommunityVersions"
import { BulkSongRow, newRow } from "./types"

interface BulkTextModeProps {
  rows: BulkSongRow[]
  onChange: (rows: BulkSongRow[]) => void
  maxSongs: number
  disabled?: boolean
}

export function BulkTextMode({ rows, onChange, maxSongs, disabled }: BulkTextModeProps) {
  const t = useTranslations("bulk")
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [checking, setChecking] = useState(false)

  // Ensure at least one empty row exists.
  useEffect(() => {
    if (rows.length === 0) onChange([newRow()])
  }, [rows.length, onChange])

  const update = (key: string, patch: Partial<BulkSongRow>) =>
    onChange(rows.map((r) => (r.key === key ? { ...r, ...patch } : r)))

  const addRow = () => {
    if (rows.length >= maxSongs) return
    onChange([...rows, newRow()])
  }

  const removeRow = (key: string) => {
    const next = rows.filter((r) => r.key !== key)
    onChange(next.length ? next : [newRow()])
  }

  // Debounced availability check for fully-filled rows lacking a result.
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current)
    const pending = rows.filter(
      (r) => r.artist.trim() && r.title.trim() && r.available == null
    )
    if (pending.length === 0) return
    debounceRef.current = setTimeout(async () => {
      setChecking(true)
      try {
        const resp = await api.checkBulkAvailability(
          pending.map((r) => ({ artist: r.artist.trim(), title: r.title.trim() }))
        )
        // Map results back to rows by (artist,title).
        const byKey = new Map(resp.results.map((r) => [`${r.artist}|||${r.title}`, r]))
        onChange(
          rows.map((r) => {
            const hit = byKey.get(`${r.artist.trim()}|||${r.title.trim()}`)
            return hit ? { ...r, available: hit.available, brands: hit.brands, versions: hit.versions } : r
          })
        )
      } catch {
        // Non-fatal: leave availability unknown.
      } finally {
        setChecking(false)
      }
    }, 700)
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows])

  return (
    <div className="space-y-2">
      <p className="text-xs" style={{ color: "var(--text-muted)" }}>{t("textModeHint", { max: maxSongs })}</p>
      {rows.map((row) => (
        <div key={row.key}>
          <div className="flex items-center gap-2">
            <Input
              placeholder={t("artist")}
              value={row.artist}
              disabled={disabled}
              onChange={(e) => update(row.key, { artist: e.target.value, available: null })}
              className="flex-1"
            />
            <Input
              placeholder={t("title")}
              value={row.title}
              disabled={disabled}
              onChange={(e) => update(row.key, { title: e.target.value, available: null })}
              className="flex-1"
            />
            <Button
              variant="ghost" size="sm" disabled={disabled}
              onClick={() => removeRow(row.key)}
              aria-label={t("removeRow")}
            >
              <X className="w-4 h-4" />
            </Button>
          </div>
          <CommunityVersions available={row.available} versions={row.versions} />
        </div>
      ))}
      <div className="flex items-center justify-between">
        <Button variant="outline" size="sm" onClick={addRow} disabled={disabled || rows.length >= maxSongs}>
          <Plus className="w-4 h-4 me-1" />{t("addSong")}
        </Button>
        {checking && <span className="text-xs" style={{ color: "var(--text-muted)" }}>{t("checking")}</span>}
      </div>
    </div>
  )
}
