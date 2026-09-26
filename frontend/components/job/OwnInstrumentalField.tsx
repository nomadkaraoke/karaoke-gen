"use client"

import { useRef, useState } from "react"
import { useTranslations } from 'next-intl'
import { AlertTriangle, Music2, Upload, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { durationsMismatch, getAudioFileDuration } from "@/lib/upload"

const ACCEPT = ".flac,.mp3,.wav,.m4a,.ogg,.aac,.aif,.aiff,.opus"

export function formatDuration(seconds: number): string {
  const total = Math.round(seconds)
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`
}

interface OwnInstrumentalFieldProps {
  /** The song (mix) file the instrumental must match in length. */
  mixFile: File
  file: File | null
  onChange: (file: File | null) => void
  disabled?: boolean
}

/**
 * Optional "bring your own instrumental" picker for private upload jobs. The
 * backend rejects instrumentals more than 0.5s longer/shorter than the mix, so
 * check that in the browser first rather than after a long upload.
 */
export function OwnInstrumentalField({ mixFile, file, onChange, disabled }: OwnInstrumentalFieldProps) {
  const t = useTranslations('jobFlow')
  const inputRef = useRef<HTMLInputElement>(null)
  const [checking, setChecking] = useState(false)
  const [error, setError] = useState("")

  async function handlePick(picked: File | undefined) {
    if (!picked) return
    setError("")
    setChecking(true)
    try {
      const [mixSeconds, instSeconds] = await Promise.all([
        getAudioFileDuration(mixFile),
        getAudioFileDuration(picked),
      ])
      if (durationsMismatch(mixSeconds, instSeconds)) {
        setError(t('ownInstrumentalMismatch', {
          instrumental: formatDuration(instSeconds as number),
          song: formatDuration(mixSeconds as number),
        }))
        onChange(null)
        return
      }
      onChange(picked)
    } finally {
      setChecking(false)
      if (inputRef.current) inputRef.current.value = ""
    }
  }

  return (
    <div
      className="space-y-3 rounded-lg border p-4"
      style={{ borderColor: 'var(--card-border)', backgroundColor: 'rgba(255,255,255,0.02)' }}
      data-testid="own-instrumental-field"
    >
      <div className="flex items-center gap-2">
        <Music2 className="w-4 h-4" style={{ color: 'var(--brand-pink)' }} />
        <h3 className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
          {t('ownInstrumentalTitle')}
        </h3>
      </div>
      <p className="text-xs" style={{ color: 'var(--text-muted)' }}>
        {t('ownInstrumentalDesc')}
      </p>

      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        className="hidden"
        data-testid="own-instrumental-input"
        onChange={(e) => handlePick(e.target.files?.[0])}
      />

      {file ? (
        <div className="flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-sm"
          style={{ borderColor: 'var(--brand-pink)', color: 'var(--text)' }}>
          <span className="truncate">
            {t('ownInstrumentalSelected', { name: file.name, size: (file.size / (1024 * 1024)).toFixed(1) })}
          </span>
          <Button type="button" variant="ghost" size="sm" disabled={disabled}
            onClick={() => { setError(""); onChange(null) }}>
            <X className="w-4 h-4 mr-1" />
            {t('ownInstrumentalRemove')}
          </Button>
        </div>
      ) : (
        <Button type="button" variant="outline" size="sm" disabled={disabled || checking}
          onClick={() => inputRef.current?.click()}>
          <Upload className="w-4 h-4 mr-2" />
          {checking ? t('ownInstrumentalChecking') : t('ownInstrumentalChoose')}
        </Button>
      )}

      {error && (
        <p className="flex items-start gap-1.5 text-xs text-red-400" role="alert">
          <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          {error}
        </p>
      )}
    </div>
  )
}
