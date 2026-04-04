"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import { useTranslations } from 'next-intl'
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ArrowLeft, Loader2, Palette, Image as ImageIcon, Paintbrush } from "lucide-react"
import { TitleCardPreview } from "../TitleCardPreview"
import { KaraokeBackgroundPreview } from "../KaraokeBackgroundPreview"
import { ImageUploadField } from "../ImageUploadField"

export interface ColorOverrides {
  artist_color?: string
  title_color?: string
  sung_lyrics_color?: string
  unsung_lyrics_color?: string
}

interface CustomizeStepProps {
  artist: string
  title: string
  displayArtist: string
  displayTitle: string
  onDisplayArtistChange: (value: string) => void
  onDisplayTitleChange: (value: string) => void
  isPrivate: boolean
  karaokeBackground: File | null
  onKaraokeBackgroundChange: (file: File | null) => void
  introBackground: File | null
  onIntroBackgroundChange: (file: File | null) => void
  colorOverrides: ColorOverrides
  onColorOverridesChange: (overrides: ColorOverrides) => void
  onConfirm: () => void
  onBack: () => void
  isSubmitting: boolean
  disabled?: boolean
}

// Preset colors for solid backgrounds
const BG_PRESETS = [
  { label: "Black", color: "#000000" },
  { label: "Dark Blue", color: "#1a1a2e" },
  { label: "Dark Purple", color: "#16213e" },
  { label: "Green Screen", color: "#00b140" },
  { label: "White", color: "#ffffff" },
]

type BgMode = "default" | "image" | "solid"

/**
 * Generate a tiny 4x4 solid color PNG blob for uploading as a background.
 * The backend expects an image file, so we create one client-side.
 */
function generateSolidColorBlob(hexColor: string): Promise<File> {
  return new Promise((resolve) => {
    const canvas = document.createElement("canvas")
    canvas.width = 4
    canvas.height = 4
    const ctx = canvas.getContext("2d")!
    ctx.fillStyle = hexColor
    ctx.fillRect(0, 0, 4, 4)
    canvas.toBlob((blob) => {
      resolve(new File([blob!], `solid-${hexColor.replace('#', '')}.png`, { type: "image/png" }))
    }, "image/png")
  })
}

function ColorPickerField({
  id,
  label,
  value,
  defaultColor,
  onChange,
  disabled,
}: {
  id: string
  label: string
  value?: string
  defaultColor: string
  onChange: (val: string | undefined) => void
  disabled?: boolean
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id} className="text-xs" style={{ color: 'var(--text-muted)' }}>
        {label}
      </Label>
      <div className="flex items-center gap-2">
        <input
          type="color"
          id={id}
          value={value || defaultColor}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
          className="w-8 h-8 rounded border cursor-pointer"
          style={{ borderColor: 'var(--card-border)' }}
        />
        <Input
          value={value || ""}
          placeholder={defaultColor}
          onChange={(e) => {
            const val = e.target.value
            if (val === "" || /^#[0-9A-Fa-f]{0,6}$/.test(val)) {
              onChange(val || undefined)
            }
          }}
          disabled={disabled}
          className="font-mono text-xs h-8"
          style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
        />
      </div>
    </div>
  )
}

function ColorPresetButtons({
  presets,
  value,
  onChange,
  disabled,
}: {
  presets: typeof BG_PRESETS
  value?: string
  onChange: (color: string) => void
  disabled?: boolean
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {presets.map((preset) => (
        <button
          key={preset.color}
          type="button"
          onClick={() => onChange(preset.color)}
          disabled={disabled}
          className={`flex items-center gap-1.5 px-2 py-1 rounded-md border text-[10px] transition-colors ${
            value === preset.color
              ? 'border-[var(--brand-pink)]'
              : 'border-[var(--card-border)] hover:border-[var(--text-muted)]'
          }`}
          style={{ backgroundColor: 'var(--secondary)' }}
          title={preset.label}
        >
          <span
            className="w-3 h-3 rounded-sm border shrink-0"
            style={{
              backgroundColor: preset.color,
              borderColor: preset.color === "#000000" ? 'var(--card-border)' : preset.color,
            }}
          />
          <span style={{ color: 'var(--text-muted)' }}>{preset.label}</span>
        </button>
      ))}
    </div>
  )
}

function BgModeToggle({
  mode,
  onChange,
  disabled,
}: {
  mode: BgMode
  onChange: (mode: BgMode) => void
  disabled?: boolean
}) {
  const options: { value: BgMode; label: string; icon: React.ReactNode }[] = [
    { value: "default", label: "Default", icon: null },
    { value: "image", label: "Image", icon: <ImageIcon className="w-3 h-3" /> },
    { value: "solid", label: "Color", icon: <Paintbrush className="w-3 h-3" /> },
  ]

  return (
    <div className="flex rounded-md border overflow-hidden" style={{ borderColor: 'var(--card-border)' }}>
      {options.map((opt) => (
        <button
          key={opt.value}
          type="button"
          onClick={() => onChange(opt.value)}
          disabled={disabled}
          className={`flex items-center gap-1 px-2.5 py-1 text-[10px] font-medium transition-colors ${
            mode === opt.value ? 'text-white' : ''
          }`}
          style={{
            backgroundColor: mode === opt.value ? 'var(--brand-pink)' : 'var(--secondary)',
            color: mode === opt.value ? 'white' : 'var(--text-muted)',
          }}
        >
          {opt.icon}
          {opt.label}
        </button>
      ))}
    </div>
  )
}

export function CustomizeStep({
  artist,
  title,
  displayArtist,
  displayTitle,
  onDisplayArtistChange,
  onDisplayTitleChange,
  isPrivate,
  karaokeBackground,
  onKaraokeBackgroundChange,
  introBackground,
  onIntroBackgroundChange,
  colorOverrides,
  onColorOverridesChange,
  onConfirm,
  onBack,
  isSubmitting,
  disabled,
}: CustomizeStepProps) {
  const t = useTranslations('jobFlow')
  const tc = useTranslations('common')
  const previewArtist = displayArtist.trim() || artist
  const previewTitle = displayTitle.trim() || title

  // Background mode state (private only)
  const [karaokeBgMode, setKaraokeBgMode] = useState<BgMode>(
    karaokeBackground ? "image" : "default"
  )
  const [introBgMode, setIntroBgMode] = useState<BgMode>(
    introBackground ? "image" : "default"
  )
  const [karaokeSolidColor, setKaraokeSolidColor] = useState("#000000")
  const [introSolidColor, setIntroSolidColor] = useState("#000000")

  // Create stable object URLs for previews and revoke on cleanup
  const prevKaraokeBlobUrl = useRef<string | undefined>(undefined)
  const prevIntroBlobUrl = useRef<string | undefined>(undefined)

  const karaokeBackgroundUrl = useMemo(
    () => karaokeBackground ? URL.createObjectURL(karaokeBackground) : undefined,
    [karaokeBackground]
  )
  const introBackgroundUrl = useMemo(
    () => introBackground ? URL.createObjectURL(introBackground) : undefined,
    [introBackground]
  )

  useEffect(() => {
    if (prevKaraokeBlobUrl.current && prevKaraokeBlobUrl.current !== karaokeBackgroundUrl) {
      URL.revokeObjectURL(prevKaraokeBlobUrl.current)
    }
    prevKaraokeBlobUrl.current = karaokeBackgroundUrl
    return () => { if (karaokeBackgroundUrl) URL.revokeObjectURL(karaokeBackgroundUrl) }
  }, [karaokeBackgroundUrl])

  useEffect(() => {
    if (prevIntroBlobUrl.current && prevIntroBlobUrl.current !== introBackgroundUrl) {
      URL.revokeObjectURL(prevIntroBlobUrl.current)
    }
    prevIntroBlobUrl.current = introBackgroundUrl
    return () => { if (introBackgroundUrl) URL.revokeObjectURL(introBackgroundUrl) }
  }, [introBackgroundUrl])

  // When switching background modes, update the file state accordingly
  function handleKaraokeBgModeChange(mode: BgMode) {
    setKaraokeBgMode(mode)
    if (mode === "default" || mode === "solid") {
      onKaraokeBackgroundChange(null)
    }
    if (mode === "solid") {
      // Generate solid color blob
      generateSolidColorBlob(karaokeSolidColor).then(onKaraokeBackgroundChange)
    }
  }

  function handleIntroBgModeChange(mode: BgMode) {
    setIntroBgMode(mode)
    if (mode === "default" || mode === "solid") {
      onIntroBackgroundChange(null)
    }
    if (mode === "solid") {
      generateSolidColorBlob(introSolidColor).then(onIntroBackgroundChange)
    }
  }

  function handleKaraokeSolidColorChange(color: string) {
    setKaraokeSolidColor(color)
    generateSolidColorBlob(color).then(onKaraokeBackgroundChange)
  }

  function handleIntroSolidColorChange(color: string) {
    setIntroSolidColor(color)
    generateSolidColorBlob(color).then(onIntroBackgroundChange)
  }

  // === PUBLIC MODE: Simple layout ===
  if (!isPrivate) {
    return (
      <div className="space-y-5">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold" style={{ color: 'var(--text)' }}>
              {t('stepLabels.customizeCreate')}
            </h2>
            <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
              {t('confirmArtistTitle')}
            </p>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={onBack}
            disabled={disabled || isSubmitting}
            style={{ color: 'var(--text-muted)' }}
          >
            <ArrowLeft className="w-4 h-4 mr-1" />
            {tc('back')}
          </Button>
        </div>

        {/* Title card preview */}
        <div className="mx-auto w-full sm:w-2/3">
          <TitleCardPreview
            artist={previewArtist}
            title={previewTitle}
          />
        </div>

        {/* Display override fields */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <Label htmlFor="guided-display-artist" className="text-xs" style={{ color: 'var(--text-muted)' }}>
              {t('titleCardArtist')}
              {!displayArtist.trim() && artist.trim() && (
                <span className="ml-1 opacity-50">{t('sameAsAbove')}</span>
              )}
            </Label>
            <Input
              id="guided-display-artist"
              placeholder={artist || t('artistOnTitleCard')}
              value={displayArtist}
              onChange={(e) => onDisplayArtistChange(e.target.value)}
              disabled={disabled || isSubmitting}
              style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="guided-display-title" className="text-xs" style={{ color: 'var(--text-muted)' }}>
              {t('titleCardTitle')}
              {!displayTitle.trim() && title.trim() && (
                <span className="ml-1 opacity-50">{t('sameAsAbove')}</span>
              )}
            </Label>
            <Input
              id="guided-display-title"
              placeholder={title || t('titleOnTitleCard')}
              value={displayTitle}
              onChange={(e) => onDisplayTitleChange(e.target.value)}
              disabled={disabled || isSubmitting}
              style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
            />
          </div>
        </div>
        <p className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
          {t('defaultsToSearchValues')}
        </p>

        <Button
          onClick={onConfirm}
          disabled={disabled || isSubmitting}
          className="w-full bg-[var(--brand-pink)] hover:bg-[var(--brand-pink-hover)] text-white shadow-[0_0_15px_rgba(255,122,204,0.3)] hover:shadow-[0_0_20px_rgba(255,122,204,0.5)] h-11 text-base"
        >
          {isSubmitting ? (
            <>
              <Loader2 className="w-4 h-4 mr-2 animate-spin" />
              {t('creating')}
            </>
          ) : (
            t('createKaraokeVideo')
          )}
        </Button>
      </div>
    )
  }

  // === PRIVATE MODE: Full customization ===
  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold" style={{ color: 'var(--text)' }}>
            {t('stepLabels.customizeCreate')}
          </h2>
          <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
            {t('confirmArtistTitle')}
          </p>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={onBack}
          disabled={disabled || isSubmitting}
          style={{ color: 'var(--text-muted)' }}
        >
          <ArrowLeft className="w-4 h-4 mr-1" />
          {tc('back')}
        </Button>
      </div>

      {/* Display override fields */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-1.5">
          <Label htmlFor="guided-display-artist" className="text-xs" style={{ color: 'var(--text-muted)' }}>
            Title Card Artist
            {!displayArtist.trim() && artist.trim() && (
              <span className="ml-1 opacity-50">(same as above)</span>
            )}
          </Label>
          <Input
            id="guided-display-artist"
            placeholder={artist || t('artistOnTitleCard')}
            value={displayArtist}
            onChange={(e) => onDisplayArtistChange(e.target.value)}
            disabled={disabled || isSubmitting}
            style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="guided-display-title" className="text-xs" style={{ color: 'var(--text-muted)' }}>
            Title Card Title
            {!displayTitle.trim() && title.trim() && (
              <span className="ml-1 opacity-50">(same as above)</span>
            )}
          </Label>
          <Input
            id="guided-display-title"
            placeholder={title || t('titleOnTitleCard')}
            value={displayTitle}
            onChange={(e) => onDisplayTitleChange(e.target.value)}
            disabled={disabled || isSubmitting}
            style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
          />
        </div>
      </div>
      <p className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
        These default to your search values. Override for soundtracks, covers, or alternate formatting.
      </p>

      {/* Custom Video Style section */}
      <div
        className="space-y-4 rounded-lg border p-4"
        style={{ borderColor: 'var(--card-border)', backgroundColor: 'rgba(255,255,255,0.02)' }}
      >
        <div className="flex items-center gap-2">
          <Palette className="w-4 h-4" style={{ color: 'var(--brand-pink)' }} />
          <h3 className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
            {t('customVideoStyle')}
          </h3>
        </div>

        {/* Side-by-side preview canvases */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {/* Title Card Preview + Controls */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <Label className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>
                {t('titleCard')}
              </Label>
              <BgModeToggle
                mode={introBgMode}
                onChange={handleIntroBgModeChange}
                disabled={disabled || isSubmitting}
              />
            </div>

            <TitleCardPreview
              artist={previewArtist}
              title={previewTitle}
              customBackgroundUrl={introBackgroundUrl}
              backgroundColor={introBgMode === "solid" ? introSolidColor : undefined}
              titleColor={colorOverrides.title_color}
              artistColor={colorOverrides.artist_color}
            />

            {introBgMode === "image" && (
              <ImageUploadField
                label=""
                description="PNG or JPG background for the title card."
                file={introBackground}
                onChange={onIntroBackgroundChange}
                disabled={disabled || isSubmitting}
                hidePreview
              />
            )}

            {introBgMode === "solid" && (
              <div className="space-y-2">
                <ColorPresetButtons
                  presets={BG_PRESETS}
                  value={introSolidColor}
                  onChange={handleIntroSolidColorChange}
                  disabled={disabled || isSubmitting}
                />
                <div className="flex items-center gap-2">
                  <input
                    type="color"
                    value={introSolidColor}
                    onChange={(e) => handleIntroSolidColorChange(e.target.value)}
                    disabled={disabled || isSubmitting}
                    className="w-8 h-8 rounded border cursor-pointer"
                    style={{ borderColor: 'var(--card-border)' }}
                  />
                  <Input
                    value={introSolidColor}
                    onChange={(e) => {
                      const val = e.target.value
                      if (/^#[0-9A-Fa-f]{0,6}$/.test(val)) {
                        handleIntroSolidColorChange(val)
                      }
                    }}
                    disabled={disabled || isSubmitting}
                    className="font-mono text-xs h-8"
                    style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
                  />
                </div>
              </div>
            )}

            {/* Title card text colors */}
            <div className="grid grid-cols-2 gap-3">
              <ColorPickerField
                id="artist-color"
                label={t('artistColor')}
                value={colorOverrides.artist_color}
                defaultColor="#ffdf6b"
                onChange={(val) => onColorOverridesChange({ ...colorOverrides, artist_color: val })}
                disabled={disabled || isSubmitting}
              />
              <ColorPickerField
                id="title-color"
                label={t('titleColor')}
                value={colorOverrides.title_color}
                defaultColor="#ffffff"
                onChange={(val) => onColorOverridesChange({ ...colorOverrides, title_color: val })}
                disabled={disabled || isSubmitting}
              />
            </div>
          </div>

          {/* Karaoke Preview + Controls */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <Label className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>
                {t('karaokeVideo')}
              </Label>
              <BgModeToggle
                mode={karaokeBgMode}
                onChange={handleKaraokeBgModeChange}
                disabled={disabled || isSubmitting}
              />
            </div>

            <KaraokeBackgroundPreview
              backgroundUrl={karaokeBackgroundUrl}
              backgroundColor={karaokeBgMode === "solid" ? karaokeSolidColor : undefined}
              sungColor={colorOverrides.sung_lyrics_color}
              unsungColor={colorOverrides.unsung_lyrics_color}
            />

            {karaokeBgMode === "image" && (
              <ImageUploadField
                label=""
                description="PNG or JPG background behind lyrics during the song."
                file={karaokeBackground}
                onChange={onKaraokeBackgroundChange}
                disabled={disabled || isSubmitting}
                hidePreview
              />
            )}

            {karaokeBgMode === "solid" && (
              <div className="space-y-2">
                <ColorPresetButtons
                  presets={BG_PRESETS}
                  value={karaokeSolidColor}
                  onChange={handleKaraokeSolidColorChange}
                  disabled={disabled || isSubmitting}
                />
                <div className="flex items-center gap-2">
                  <input
                    type="color"
                    value={karaokeSolidColor}
                    onChange={(e) => handleKaraokeSolidColorChange(e.target.value)}
                    disabled={disabled || isSubmitting}
                    className="w-8 h-8 rounded border cursor-pointer"
                    style={{ borderColor: 'var(--card-border)' }}
                  />
                  <Input
                    value={karaokeSolidColor}
                    onChange={(e) => {
                      const val = e.target.value
                      if (/^#[0-9A-Fa-f]{0,6}$/.test(val)) {
                        handleKaraokeSolidColorChange(val)
                      }
                    }}
                    disabled={disabled || isSubmitting}
                    className="font-mono text-xs h-8"
                    style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
                  />
                </div>
              </div>
            )}

            {/* Lyrics colors */}
            <div className="grid grid-cols-2 gap-3">
              <ColorPickerField
                id="sung-lyrics-color"
                label={t('highlightColor')}
                value={colorOverrides.sung_lyrics_color}
                defaultColor="#7070F7"
                onChange={(val) => onColorOverridesChange({ ...colorOverrides, sung_lyrics_color: val })}
                disabled={disabled || isSubmitting}
              />
              <ColorPickerField
                id="unsung-lyrics-color"
                label={t('lyricsColor')}
                value={colorOverrides.unsung_lyrics_color}
                defaultColor="#ffffff"
                onChange={(val) => onColorOverridesChange({ ...colorOverrides, unsung_lyrics_color: val })}
                disabled={disabled || isSubmitting}
              />
            </div>
          </div>
        </div>

        <p className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
          {t('leaveColorsBlank')}
        </p>
      </div>

      {/* Create button */}
      <Button
        onClick={onConfirm}
        disabled={disabled || isSubmitting}
        className="w-full bg-[var(--brand-pink)] hover:bg-[var(--brand-pink-hover)] text-white shadow-[0_0_15px_rgba(255,122,204,0.3)] hover:shadow-[0_0_20px_rgba(255,122,204,0.5)] h-11 text-base"
      >
        {isSubmitting ? (
          <>
            <Loader2 className="w-4 h-4 mr-2 animate-spin" />
            Creating...
          </>
        ) : (
          "Create Karaoke Video"
        )}
      </Button>
    </div>
  )
}
