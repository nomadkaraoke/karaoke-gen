"use client"

import { useEffect, useMemo, useRef } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ArrowLeft, Loader2, Palette } from "lucide-react"
import { TitleCardPreview } from "../TitleCardPreview"
import { KaraokeBackgroundPreview } from "../KaraokeBackgroundPreview"
import { ImageUploadField } from "../ImageUploadField"

export interface ColorOverrides {
  artist_color?: string
  title_color?: string
}

interface CustomizeStepProps {
  artist: string
  title: string
  displayArtist: string
  displayTitle: string
  onDisplayArtistChange: (value: string) => void
  onDisplayTitleChange: (value: string) => void
  isPrivate: boolean
  onPrivateChange: (value: boolean) => void
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

export function CustomizeStep({
  artist,
  title,
  displayArtist,
  displayTitle,
  onDisplayArtistChange,
  onDisplayTitleChange,
  isPrivate,
  onPrivateChange,
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
  // The displayed artist/title on the title card
  const previewArtist = displayArtist.trim() || artist
  const previewTitle = displayTitle.trim() || title

  // Create stable object URLs for previews and revoke on cleanup
  const prevKaraokeBlobUrl = useRef<string>()
  const prevIntroBlobUrl = useRef<string>()

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

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold" style={{ color: 'var(--text)' }}>
            Customize & Create
          </h2>
          <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
            Preview your title card and confirm your settings.
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
          Back
        </Button>
      </div>

      {/* Title card preview - half width, centered */}
      <div className="space-y-2">
        <Label className="text-xs" style={{ color: 'var(--text-muted)' }}>Title Card Preview</Label>
        <div className="mx-auto w-full sm:w-1/2">
          <TitleCardPreview
            artist={previewArtist}
            title={previewTitle}
            customBackgroundUrl={introBackgroundUrl}
            titleColor={colorOverrides.title_color}
            artistColor={colorOverrides.artist_color}
          />
        </div>
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
            placeholder={artist || "Artist on title card"}
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
            placeholder={title || "Title on title card"}
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

      {/* Privacy toggle */}
      <div className="space-y-2 pt-2 border-t" style={{ borderColor: 'var(--card-border)' }}>
        <div className="flex items-start gap-3">
          <input
            type="checkbox"
            id="guided-private"
            checked={isPrivate}
            onChange={(e) => onPrivateChange(e.target.checked)}
            disabled={disabled || isSubmitting}
            className="mt-1 w-4 h-4 rounded border-border bg-secondary accent-[var(--brand-pink)] focus:ring-[var(--brand-pink)] focus:ring-offset-background"
          />
          <div>
            <Label htmlFor="guided-private" className="cursor-pointer" style={{ color: 'var(--text)' }}>
              Private (no YouTube upload)
            </Label>
            <p className="text-xs mt-1" style={{ color: 'var(--text-muted)' }}>
              Files delivered via Dropbox only. No YouTube or Google Drive upload.
            </p>
          </div>
        </div>
      </div>

      {/* Custom Video Style section - only shown for private delivery */}
      {isPrivate && (
        <div
          className="space-y-4 rounded-lg border p-4"
          style={{ borderColor: 'var(--card-border)', backgroundColor: 'rgba(255,255,255,0.02)' }}
        >
          <div className="flex items-center gap-2">
            <Palette className="w-4 h-4" style={{ color: 'var(--brand-pink)' }} />
            <h3 className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
              Custom Video Style
            </h3>
          </div>
          <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
            Upload custom backgrounds and choose colors for your branded karaoke video.
          </p>

          {/* Karaoke Background */}
          <div className="space-y-2">
            <ImageUploadField
              label="Karaoke Background"
              description="Replaces the starfield behind lyrics during the song."
              file={karaokeBackground}
              onChange={onKaraokeBackgroundChange}
              disabled={disabled || isSubmitting}
            />
            {karaokeBackgroundUrl && (
              <div className="mx-auto w-full sm:w-2/3">
                <KaraokeBackgroundPreview backgroundUrl={karaokeBackgroundUrl} />
              </div>
            )}
          </div>

          {/* Title Card Background */}
          <div className="space-y-2">
            <ImageUploadField
              label="Title Card Background"
              description="Background image for the intro/title card shown before the song starts."
              file={introBackground}
              onChange={onIntroBackgroundChange}
              disabled={disabled || isSubmitting}
            />
          </div>

          {/* Color overrides */}
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label htmlFor="artist-color" className="text-xs" style={{ color: 'var(--text-muted)' }}>
                Artist Color
              </Label>
              <div className="flex items-center gap-2">
                <input
                  type="color"
                  id="artist-color"
                  value={colorOverrides.artist_color || "#ffdf6b"}
                  onChange={(e) => onColorOverridesChange({ ...colorOverrides, artist_color: e.target.value })}
                  disabled={disabled || isSubmitting}
                  className="w-8 h-8 rounded border cursor-pointer"
                  style={{ borderColor: 'var(--card-border)' }}
                />
                <Input
                  value={colorOverrides.artist_color || ""}
                  placeholder="#ffdf6b"
                  onChange={(e) => {
                    const val = e.target.value
                    if (val === "" || /^#[0-9A-Fa-f]{0,6}$/.test(val)) {
                      onColorOverridesChange({ ...colorOverrides, artist_color: val || undefined })
                    }
                  }}
                  disabled={disabled || isSubmitting}
                  className="font-mono text-xs h-8"
                  style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
                />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="title-color" className="text-xs" style={{ color: 'var(--text-muted)' }}>
                Title Color
              </Label>
              <div className="flex items-center gap-2">
                <input
                  type="color"
                  id="title-color"
                  value={colorOverrides.title_color || "#ffffff"}
                  onChange={(e) => onColorOverridesChange({ ...colorOverrides, title_color: e.target.value })}
                  disabled={disabled || isSubmitting}
                  className="w-8 h-8 rounded border cursor-pointer"
                  style={{ borderColor: 'var(--card-border)' }}
                />
                <Input
                  value={colorOverrides.title_color || ""}
                  placeholder="#ffffff"
                  onChange={(e) => {
                    const val = e.target.value
                    if (val === "" || /^#[0-9A-Fa-f]{0,6}$/.test(val)) {
                      onColorOverridesChange({ ...colorOverrides, title_color: val || undefined })
                    }
                  }}
                  disabled={disabled || isSubmitting}
                  className="font-mono text-xs h-8"
                  style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
                />
              </div>
            </div>
          </div>
          <p className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
            Leave blank to use default Nomad theme colors.
          </p>
        </div>
      )}

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
