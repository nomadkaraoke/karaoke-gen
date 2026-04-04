"use client"

import { useTranslations } from 'next-intl'
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Button } from "@/components/ui/button"
import { ArrowRight, Lightbulb, Pencil, Search, Upload, Youtube } from "lucide-react"

interface SongInfoStepProps {
  artist: string
  title: string
  onArtistChange: (value: string) => void
  onTitleChange: (value: string) => void
  onNext: () => void
  disabled?: boolean
}

export function SongInfoStep({
  artist,
  title,
  onArtistChange,
  onTitleChange,
  onNext,
  disabled,
}: SongInfoStepProps) {
  const t = useTranslations('jobFlow')
  const tc = useTranslations('common')
  const canProceed = artist.trim() && title.trim()

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (canProceed) onNext()
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-5">
      {/* How it works - compact visual */}
      <div className="flex items-center gap-3 text-xs flex-wrap" style={{ color: 'var(--text-muted)' }}>
        <div className="flex items-center gap-1.5">
          <Search className="w-3.5 h-3.5" style={{ color: 'var(--brand-pink)' }} />
          <span>{t('weFindBestAudio')}</span>
        </div>
        <span className="opacity-40">{tc('or')}</span>
        <div className="flex items-center gap-1.5">
          <Upload className="w-3.5 h-3.5 text-amber-400" />
          <span>{t('uploadAFile')}</span>
        </div>
        <span className="opacity-40">/</span>
        <div className="flex items-center gap-1.5">
          <Youtube className="w-3.5 h-3.5 text-red-400" />
          <span>{t('youtubeUrl')}</span>
        </div>
        <span className="opacity-50">{t('onNextPage')}</span>
      </div>

      {/* Search artist/title */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="guided-artist" style={{ color: 'var(--text)' }}>{t('artist')}</Label>
          <Input
            id="guided-artist"
            data-testid="guided-artist-input"
            placeholder={t('artistPlaceholder')}
            value={artist}
            onChange={(e) => onArtistChange(e.target.value)}
            disabled={disabled}
            autoFocus
            style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="guided-title" style={{ color: 'var(--text)' }}>{t('title')}</Label>
          <Input
            id="guided-title"
            data-testid="guided-title-input"
            placeholder={t('titlePlaceholder')}
            value={title}
            onChange={(e) => onTitleChange(e.target.value)}
            disabled={disabled}
            style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
          />
        </div>
      </div>

      {/* Tip callout */}
      <div
        className="flex items-start gap-2.5 rounded-lg p-3 text-xs"
        style={{ backgroundColor: 'var(--secondary)', color: 'var(--text-muted)' }}
      >
        <Lightbulb className="w-4 h-4 shrink-0 mt-0.5 text-amber-400" />
        <p style={{ whiteSpace: 'pre-line' }}>
          {t('tipCallout')}
        </p>
      </div>

      {/* Custom lyrics note */}
      <div
        className="flex items-start gap-2.5 rounded-lg p-3 text-xs"
        style={{ backgroundColor: 'var(--secondary)', color: 'var(--text-muted)' }}
      >
        <Pencil className="w-4 h-4 shrink-0 mt-0.5 text-purple-400" />
        <p>
          {t('customLyricsNote')}
        </p>
      </div>

      {/* Unreleased / own audio note */}
      <div
        className="flex items-start gap-2.5 rounded-lg p-3 text-xs"
        style={{ backgroundColor: 'var(--secondary)', color: 'var(--text-muted)' }}
      >
        <Upload className="w-4 h-4 shrink-0 mt-0.5 text-blue-400" />
        <p>
          {t('unreleasedNote')}
        </p>
      </div>

      <Button
        type="submit"
        disabled={!canProceed || disabled}
        className="w-full bg-[var(--brand-pink)] hover:bg-[var(--brand-pink-hover)] text-white shadow-[0_0_15px_rgba(255,122,204,0.3)] hover:shadow-[0_0_20px_rgba(255,122,204,0.5)]"
      >
        {t('stepLabels.chooseAudio')}
        <ArrowRight className="w-4 h-4 ml-2" />
      </Button>
    </form>
  )
}
