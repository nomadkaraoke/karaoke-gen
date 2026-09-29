"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useTranslations } from "next-intl"
import { AlertTriangle, ImageIcon, Loader2, Palette, Upload } from "lucide-react"
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useToast } from "@/hooks/use-toast"
import { tenantThemeApi, type ThemePreviewImages, type ThemeStyleParams } from "@/lib/api"
import { useTenant } from "@/lib/tenant"
import {
  KARAOKE_DEFAULTS, formatRegion, getField, hexToRgba, parseRegion, rgbaToHex, setField, stableStringify,
  type Region,
} from "./theme-utils"

const PREVIEW_DEBOUNCE_MS = 700
const selectClass =
  "h-9 w-full rounded-md border border-input bg-transparent px-2 text-sm focus:outline-none focus:ring-1 focus:ring-ring"

interface Props {
  open: boolean
  onClose: () => void
}

type Tab = "titleCard" | "karaoke" | "endScreen" | "advanced"

// --- small field components ---------------------------------------------------

function ColorInput({ id, label, value, onChange }: {
  id: string; label: string; value: string; onChange: (hex: string) => void
}) {
  return (
    <div className="space-y-1">
      <Label htmlFor={id} className="text-xs text-muted-foreground">{label}</Label>
      <div className="flex items-center gap-2">
        <input
          type="color"
          aria-label={label}
          value={/^#[0-9a-f]{6}$/i.test(value) ? value : "#000000"}
          onChange={(e) => onChange(e.target.value)}
          className="h-9 w-9 shrink-0 cursor-pointer rounded border border-input bg-transparent p-0.5"
        />
        <Input id={id} value={value} onChange={(e) => onChange(e.target.value)} className="font-mono" />
      </div>
    </div>
  )
}

function RangeInput({ id, label, value, min, max, step = 1, onChange }: {
  id: string; label: string; value: number; min: number; max: number; step?: number; onChange: (n: number) => void
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <Label htmlFor={id} className="text-xs text-muted-foreground">{label}</Label>
        <span className="text-xs font-mono text-muted-foreground">{value}</span>
      </div>
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-[var(--brand-pink,#ff5bb8)]"
      />
    </div>
  )
}

// --- main dialog ----------------------------------------------------------------

export function TenantThemeEditorDialog({ open, onClose }: Props) {
  const t = useTranslations("tenantTheme")
  const { toast } = useToast()
  const { tenant } = useTenant()

  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saved, setSaved] = useState<ThemeStyleParams | null>(null)
  const [draft, setDraft] = useState<ThemeStyleParams | null>(null)
  const [images, setImages] = useState<string[]>([])
  const [fonts, setFonts] = useState<string[]>([])
  const [tab, setTab] = useState<Tab>("titleCard")
  const [jsonText, setJsonText] = useState("")
  const [jsonError, setJsonError] = useState<string | null>(null)
  const [sampleArtist, setSampleArtist] = useState("")
  const [sampleTitle, setSampleTitle] = useState("")
  const [preview, setPreview] = useState<ThemePreviewImages | null>(null)
  const [previewing, setPreviewing] = useState(false)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [uploading, setUploading] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  // Load the current theme each time the dialog opens.
  useEffect(() => {
    if (!open) return
    let cancelled = false
    setLoading(true)
    setLoadError(null)
    setSampleArtist(tenant?.name || t("sampleArtistDefault"))
    setSampleTitle(t("sampleTitleDefault"))
    tenantThemeApi
      .get()
      .then((data) => {
        if (cancelled) return
        setSaved(data.style_params)
        setDraft(data.style_params)
        setJsonText(JSON.stringify(data.style_params, null, 2))
        setImages(data.images)
        setFonts(data.fonts)
      })
      .catch((err: any) => !cancelled && setLoadError(err?.message || t("loadFailed")))
      .finally(() => !cancelled && setLoading(false))
    return () => { cancelled = true }
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  const dirty = useMemo(
    () => !!draft && !!saved && stableStringify(draft) !== stableStringify(saved),
    [draft, saved],
  )

  // Debounced exact server preview; stale requests are aborted.
  useEffect(() => {
    if (!open || !draft) return
    // A render of an older draft is now stale — cancel it immediately so its image
    // can't flash in while the debounce waits.
    abortRef.current?.abort()
    setPreviewing(true)
    const handle = setTimeout(async () => {
      const controller = new AbortController()
      abortRef.current = controller
      setPreviewing(true)
      setPreviewError(null)
      try {
        const result = await tenantThemeApi.preview(
          draft,
          { artist: sampleArtist || undefined, title: sampleTitle || undefined },
          controller.signal,
        )
        if (!controller.signal.aborted) setPreview(result)
      } catch (err: any) {
        if (!controller.signal.aborted && err?.name !== "AbortError") {
          setPreviewError(err?.message || t("previewFailed"))
        }
      } finally {
        if (!controller.signal.aborted) setPreviewing(false)
      }
    }, PREVIEW_DEBOUNCE_MS)
    return () => clearTimeout(handle)
  }, [open, draft, sampleArtist, sampleTitle]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => () => abortRef.current?.abort(), [])

  const update = useCallback((section: string, field: string, value: unknown) => {
    setDraft((d) => (d ? setField(d, section, field, value) : d))
  }, [])

  const switchTab = (next: Tab) => {
    if (tab === "advanced" && next !== "advanced") {
      // Leaving the JSON tab applies it (only if valid).
      try {
        const parsed = JSON.parse(jsonText)
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error(t("jsonMustBeObject"))
        setDraft(parsed)
        setJsonError(null)
      } catch (e: any) {
        setJsonError(e?.message || t("jsonInvalid"))
        return
      }
    }
    if (next === "advanced" && draft) setJsonText(JSON.stringify(draft, null, 2))
    setTab(next)
  }

  const onJsonChange = (text: string) => {
    setJsonText(text)
    try {
      const parsed = JSON.parse(text)
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error(t("jsonMustBeObject"))
      setJsonError(null)
      setDraft(parsed)
    } catch (e: any) {
      setJsonError(e?.message || t("jsonInvalid"))
    }
  }

  const uploadAsset = async (file: File, apply: (name: string) => void, key: string) => {
    setUploading(key)
    try {
      const { name } = await tenantThemeApi.uploadAsset(file)
      if (/\.(ttf|otf)$/i.test(name)) setFonts((f) => Array.from(new Set([...f, name])).sort())
      else setImages((imgs) => Array.from(new Set([...imgs, name])).sort())
      apply(name)
    } catch (err: any) {
      toast({ title: t("uploadFailed"), description: err?.message, variant: "destructive" })
    } finally {
      setUploading(null)
    }
  }

  const handleSave = async () => {
    if (!draft || jsonError) return
    setSaving(true)
    try {
      const data = await tenantThemeApi.save(draft)
      setSaved(data.style_params)
      setDraft(data.style_params)
      setImages(data.images)
      setFonts(data.fonts)
      toast({ title: t("savedTitle"), description: t("savedBody") })
    } catch (err: any) {
      toast({ title: t("saveFailed"), description: err?.message, variant: "destructive" })
    } finally {
      setSaving(false)
    }
  }

  const handleClose = () => {
    if (dirty && !window.confirm(t("discardConfirm"))) return
    abortRef.current?.abort()
    onClose()
  }

  // --- guided-form helpers ------------------------------------------------------

  const bgField = (section: "intro" | "karaoke" | "end") => {
    const image = getField<string | null>(draft!, section, "background_image") || ""
    const color = getField<string>(draft!, section, "background_color") || "#000000"
    const key = `${section}-bg`
    return (
      <div className="space-y-2 rounded-md border p-3">
        <Label className="flex items-center gap-1.5"><ImageIcon className="h-3.5 w-3.5" /> {t("background")}</Label>
        <select
          aria-label={t("backgroundImage")}
          className={selectClass}
          value={image}
          onChange={(e) => update(section, "background_image", e.target.value || null)}
        >
          <option value="">{t("solidColour")}</option>
          {images.map((img) => <option key={img} value={img}>{img}</option>)}
        </select>
        <div className="flex items-center gap-2">
          <label className="inline-flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground">
            {uploading === key ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
            {t("uploadImage")}
            <input
              type="file"
              className="sr-only"
              aria-label={`${t("uploadImage")} (${t(`tabs.${section === "intro" ? "titleCard" : section === "end" ? "endScreen" : "karaoke"}`)})`}
              accept=".png,.jpg,.jpeg,.gif,.webp"
              disabled={!!uploading}
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file) uploadAsset(file, (name) => update(section, "background_image", name), key)
                e.target.value = ""
              }}
            />
          </label>
        </div>
        {!image && (
          <ColorInput id={`${section}-bg-colour`} label={t("backgroundColour")} value={color}
            onChange={(v) => update(section, "background_color", v)} />
        )}
      </div>
    )
  }

  const regionSliders = (section: "intro" | "end", field: "title_region" | "artist_region" | "extra_text_region", label: string, fallback: Region) => {
    const region = parseRegion(getField(draft!, section, field), fallback)
    return (
      <div className="grid grid-cols-2 gap-3">
        <RangeInput id={`${section}-${field}-y`} label={t("positionFromTop", { item: label })} value={region.y} min={0} max={2000} step={10}
          onChange={(y) => update(section, field, formatRegion({ ...region, y }))} />
        <RangeInput id={`${section}-${field}-h`} label={t("textSize", { item: label })} value={region.h} min={60} max={1000} step={10}
          onChange={(h) => update(section, field, formatRegion({ ...region, h }))} />
      </div>
    )
  }

  const upperToggle = (section: "intro" | "end", field: string, label: string) => (
    <label className="flex items-center gap-2 text-sm">
      <input
        type="checkbox"
        checked={getField(draft!, section, field) === "uppercase"}
        onChange={(e) => update(section, field, e.target.checked ? "uppercase" : null)}
      />
      {label}
    </label>
  )

  const fontField = () => {
    const font = getField<string>(draft!, "intro", "font") || ""
    return (
      <div className="space-y-2 rounded-md border p-3">
        <Label htmlFor="theme-font">{t("font")}</Label>
        <select
          id="theme-font"
          className={selectClass}
          value={font}
          onChange={(e) => update("intro", "font", e.target.value)}
        >
          {!fonts.includes(font) && font && <option value={font}>{font}</option>}
          {fonts.map((f) => <option key={f} value={f}>{f.replace(/\.(ttf|otf)$/i, "")}</option>)}
        </select>
        <label className="inline-flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground">
          {uploading === "font" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
          {t("uploadFont")}
          <input
            type="file"
            className="sr-only"
            aria-label={t("uploadFont")}
            accept=".ttf,.otf"
            disabled={!!uploading}
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) uploadAsset(file, (name) => update("intro", "font", name), "font")
              e.target.value = ""
            }}
          />
        </label>
        <p className="text-xs text-muted-foreground">{t("fontHint")}</p>
      </div>
    )
  }

  const karaokeColour = (field: string, label: string) => (
    <ColorInput
      id={`karaoke-${field}`}
      label={label}
      value={rgbaToHex(getField(draft!, "karaoke", field))}
      onChange={(hex) => update("karaoke", field, hexToRgba(hex, getField(draft!, "karaoke", field)))}
    />
  )

  const tabs: Tab[] = ["titleCard", "karaoke", "endScreen", "advanced"]

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) handleClose() }}>
      <DialogContent className="max-w-6xl max-h-[92vh] overflow-y-auto" data-testid="tenant-theme-editor">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2"><Palette className="h-5 w-5" /> {t("title")}</DialogTitle>
          <DialogDescription>{t("description")}</DialogDescription>
        </DialogHeader>

        {loading || (!draft && !loadError) ? (
          <div className="flex items-center justify-center py-16 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin mr-2" /> {t("loading")}
          </div>
        ) : loadError ? (
          <p className="py-10 text-center text-destructive">{loadError}</p>
        ) : (
          <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
            {/* Editor */}
            <div className="space-y-4 min-w-0">
              <div role="tablist" className="flex flex-wrap gap-1 rounded-md bg-secondary/50 p-1">
                {tabs.map((id) => (
                  <button
                    key={id}
                    role="tab"
                    type="button"
                    aria-selected={tab === id}
                    onClick={() => switchTab(id)}
                    className={`flex-1 rounded px-3 py-1.5 text-sm transition-colors ${tab === id ? "bg-background shadow text-foreground" : "text-muted-foreground hover:text-foreground"}`}
                  >
                    {t(`tabs.${id}`)}
                  </button>
                ))}
              </div>

              {tab === "titleCard" && (
                <div className="space-y-4">
                  {bgField("intro")}
                  <div className="grid grid-cols-2 gap-3">
                    <ColorInput id="intro-title-colour" label={t("titleColour")} value={getField<string>(draft!, "intro", "title_color") || "#ffffff"}
                      onChange={(v) => update("intro", "title_color", v)} />
                    <ColorInput id="intro-artist-colour" label={t("artistColour")} value={getField<string>(draft!, "intro", "artist_color") || "#ffdf6b"}
                      onChange={(v) => update("intro", "artist_color", v)} />
                  </div>
                  {regionSliders("intro", "title_region", t("songTitle"), { x: 370, y: 980, w: 3100, h: 350 })}
                  {regionSliders("intro", "artist_region", t("artistName"), { x: 370, y: 1400, w: 3100, h: 450 })}
                  <div className="flex flex-wrap gap-4">
                    {upperToggle("intro", "title_text_transform", t("uppercaseTitle"))}
                    {upperToggle("intro", "artist_text_transform", t("uppercaseArtist"))}
                  </div>
                  {fontField()}
                </div>
              )}

              {tab === "karaoke" && (
                <div className="space-y-4">
                  {bgField("karaoke")}
                  <div className="grid grid-cols-3 gap-3">
                    {karaokeColour("primary_color", t("sungColour"))}
                    {karaokeColour("secondary_color", t("unsungColour"))}
                    {karaokeColour("outline_color", t("outlineColour"))}
                  </div>
                  <RangeInput id="karaoke-font-size" label={t("lyricsSize")} min={60} max={400} step={5}
                    value={getField<number>(draft!, "karaoke", "font_size") ?? KARAOKE_DEFAULTS.font_size}
                    onChange={(n) => update("karaoke", "font_size", n)} />
                  <RangeInput id="karaoke-top-padding" label={t("lyricsPosition")} min={0} max={1500} step={10}
                    value={getField<number>(draft!, "karaoke", "top_padding") ?? (getField<number>(draft!, "karaoke", "font_size") ?? KARAOKE_DEFAULTS.font_size)}
                    onChange={(n) => update("karaoke", "top_padding", n)} />
                  <RangeInput id="karaoke-line-length" label={t("lineLength")} min={15} max={60}
                    value={getField<number>(draft!, "karaoke", "max_line_length") ?? KARAOKE_DEFAULTS.max_line_length}
                    onChange={(n) => update("karaoke", "max_line_length", n)} />
                  {fontField()}
                </div>
              )}

              {tab === "endScreen" && (
                <div className="space-y-4">
                  {bgField("end")}
                  <div className="space-y-1">
                    <Label htmlFor="end-extra-text">{t("closingMessage")}</Label>
                    <Input id="end-extra-text" value={getField<string>(draft!, "end", "extra_text") || ""}
                      onChange={(e) => update("end", "extra_text", e.target.value || null)} />
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <ColorInput id="end-extra-colour" label={t("closingMessageColour")} value={getField<string>(draft!, "end", "extra_text_color") || "#ffffff"}
                      onChange={(v) => update("end", "extra_text_color", v)} />
                    <ColorInput id="end-title-colour" label={t("titleColour")} value={getField<string>(draft!, "end", "title_color") || "#ffffff"}
                      onChange={(v) => update("end", "title_color", v)} />
                  </div>
                  <p className="text-xs text-muted-foreground">{t("endScreenHint")}</p>
                </div>
              )}

              {tab === "advanced" && (
                <div className="space-y-2">
                  <textarea
                    aria-label={t("advancedJson")}
                    value={jsonText}
                    onChange={(e) => onJsonChange(e.target.value)}
                    spellCheck={false}
                    className="h-[420px] w-full rounded-md border border-input bg-transparent p-2 font-mono text-xs"
                  />
                  {jsonError
                    ? <p className="text-xs text-destructive">{jsonError}</p>
                    : <p className="text-xs text-muted-foreground">{t("advancedHint")}</p>}
                </div>
              )}
            </div>

            {/* Previews */}
            <div className="space-y-3 min-w-0">
              <div className="grid grid-cols-2 gap-2">
                <div className="space-y-1">
                  <Label htmlFor="sample-artist" className="text-xs text-muted-foreground">{t("sampleArtist")}</Label>
                  <Input id="sample-artist" value={sampleArtist} onChange={(e) => setSampleArtist(e.target.value)} />
                </div>
                <div className="space-y-1">
                  <Label htmlFor="sample-title" className="text-xs text-muted-foreground">{t("sampleTitle")}</Label>
                  <Input id="sample-title" value={sampleTitle} onChange={(e) => setSampleTitle(e.target.value)} />
                </div>
              </div>
              {(["title_card", "karaoke_frame"] as const).map((kind) => (
                <figure key={kind} className="space-y-1">
                  <figcaption className="text-xs font-medium text-muted-foreground">
                    {kind === "title_card" ? t("previewTitleCard") : t("previewKaraoke")}
                  </figcaption>
                  <div className="relative aspect-video overflow-hidden rounded-md border bg-black">
                    {preview?.[kind] && (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={preview[kind]} alt={kind === "title_card" ? t("previewTitleCard") : t("previewKaraoke")}
                        data-testid={`preview-${kind}`} className="h-full w-full object-contain" />
                    )}
                    {previewing && (
                      <div className="absolute inset-0 flex items-center justify-center bg-black/40">
                        <Loader2 className="h-6 w-6 animate-spin text-white" aria-label={t("rendering")} />
                      </div>
                    )}
                  </div>
                </figure>
              ))}
              {previewError && (
                <p className="flex items-start gap-1.5 text-xs text-destructive">
                  <AlertTriangle className="h-3.5 w-3.5 mt-0.5 shrink-0" /> {previewError}
                </p>
              )}
              <p className="text-xs text-muted-foreground">{t("previewHint")}</p>
            </div>
          </div>
        )}

        <DialogFooter className="flex items-center gap-2 sm:justify-between">
          <p className="text-xs text-muted-foreground">{t("appliesToNewJobs")}</p>
          <div className="flex gap-2">
            <Button variant="ghost" onClick={() => { if (saved) { setDraft(saved); setJsonText(JSON.stringify(saved, null, 2)); setJsonError(null) } }}
              disabled={!dirty || saving}>
              {t("discard")}
            </Button>
            <Button variant="ghost" onClick={handleClose} disabled={saving}>{t("close")}</Button>
            <Button onClick={handleSave} disabled={!dirty || saving || !!jsonError || !!uploading}>
              {saving ? <><Loader2 className="h-4 w-4 mr-2 animate-spin" /> {t("saving")}</> : t("save")}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
