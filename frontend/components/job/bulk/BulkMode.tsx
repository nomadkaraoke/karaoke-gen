"use client"

import { useMemo, useState } from "react"
import { useTranslations } from "next-intl"
import { api, BulkSettings } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs"
import { BulkTextMode } from "./BulkTextMode"
import { BulkAlbumMode } from "./BulkAlbumMode"
import { BulkBatchSettings } from "./BulkBatchSettings"
import { BulkSubmitBar } from "./BulkSubmitBar"
import { BulkBatchProgress } from "./BulkBatchProgress"
import { BulkSongRow } from "./types"

const MAX_SONGS = 100

const DEFAULT_SETTINGS: BulkSettings = {
  auto_select_if_lossless: true,
  is_private: false,
  skip_audio_edit: true,
  skip_customization: true,
}

interface BulkModeProps {
  onJobsChanged?: () => void
  onBuyCredits?: () => void
}

export function BulkMode({ onJobsChanged, onBuyCredits }: BulkModeProps) {
  const t = useTranslations("bulk")
  const { user } = useAuth()
  const isAdmin = user?.role === "admin" || !!user?.email?.endsWith("@nomadkaraoke.com")
  const credits = user?.credits ?? null

  const [subMode, setSubMode] = useState<"text" | "album">("album")
  const [textRows, setTextRows] = useState<BulkSongRow[]>([])
  const [albumRows, setAlbumRows] = useState<BulkSongRow[]>([])
  const [settings, setSettings] = useState<BulkSettings>(DEFAULT_SETTINGS)
  const [isSubmitting, setIsSubmitting] = useState(false)
  // Persist the last batch so a returning user can finish its review queue
  // (parked awaiting-selection jobs are hidden from the main jobs list).
  const [batchId, setBatchId] = useState<string | null>(() => {
    if (typeof window === "undefined") return null
    return localStorage.getItem("nomad-bulk-last-batch")
  })
  const [error, setError] = useState("")

  const activeRows = subMode === "text" ? textRows : albumRows
  const selectedRows = useMemo(
    () => activeRows.filter((r) => r.selected && r.artist.trim() && r.title.trim()),
    [activeRows]
  )

  async function handleSubmit() {
    setIsSubmitting(true)
    setError("")
    try {
      const resp = await api.bulkSubmit({
        songs: selectedRows.map((r) => ({
          artist: r.artist.trim(),
          title: r.title.trim(),
          display_artist: r.display_artist?.trim() || undefined,
          display_title: r.display_title?.trim() || undefined,
        })),
        settings,
      })
      setBatchId(resp.batch_id)
      if (typeof window !== "undefined") localStorage.setItem("nomad-bulk-last-batch", resp.batch_id)
      onJobsChanged?.()
    } catch (e: any) {
      setError(e?.message || t("submitError"))
    } finally {
      setIsSubmitting(false)
    }
  }

  function startAnother() {
    setBatchId(null)
    if (typeof window !== "undefined") localStorage.removeItem("nomad-bulk-last-batch")
    setTextRows([])
    setAlbumRows([])
  }

  if (batchId) {
    return (
      <BulkBatchProgress
        batchId={batchId}
        onStartAnother={startAnother}
        onJobsChanged={onJobsChanged}
      />
    )
  }

  return (
    <div className="space-y-4">
      <Tabs value={subMode} onValueChange={(v) => setSubMode(v as "text" | "album")}>
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="album">{t("byAlbum")}</TabsTrigger>
          <TabsTrigger value="text">{t("byText")}</TabsTrigger>
        </TabsList>
        <TabsContent value="album" className="mt-3">
          <BulkAlbumMode rows={albumRows} onChange={setAlbumRows} disabled={isSubmitting} />
        </TabsContent>
        <TabsContent value="text" className="mt-3">
          <BulkTextMode rows={textRows} onChange={setTextRows} maxSongs={MAX_SONGS} disabled={isSubmitting} />
        </TabsContent>
      </Tabs>

      <BulkBatchSettings settings={settings} onChange={setSettings} disabled={isSubmitting} />

      {error && <p className="text-sm" style={{ color: "#ef4444" }}>{error}</p>}

      <BulkSubmitBar
        selectedCount={selectedRows.length}
        credits={credits}
        isAdmin={isAdmin}
        isSubmitting={isSubmitting}
        onSubmit={handleSubmit}
        onBuyCredits={onBuyCredits}
        maxSongs={MAX_SONGS}
      />
    </div>
  )
}
