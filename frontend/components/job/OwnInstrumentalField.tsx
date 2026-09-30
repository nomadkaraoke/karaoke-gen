"use client"

import { useRef, useState } from "react"
import { useTranslations } from 'next-intl'
import { AlertTriangle, Music2, Upload, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { checkInstrumentalFile } from "@/lib/upload"

// Must match the backend allow-list for existing_instrumental (ALLOWED_AUDIO_EXTENSIONS).
const ACCEPT = ".flac,.mp3,.wav,.m4a,.ogg,.aac"

export function formatDuration(seconds: number): string {
  const total = Math.round(seconds)
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`
}

interface OwnInstrumentalFieldProps {
  file: File | null
  onChange: (file: File | null) => void
  disabled?: boolean
}

/**
 * Optional "bring your own instrumental" picker for private upload jobs. Only
 * the size cap is checked here: an instrumental of a different length is lined
 * up with the mix by the backend when the upload completes.
 */
export function OwnInstrumentalField({ file, onChange, disabled }: OwnInstrumentalFieldProps) {
  const t = useTranslations('jobFlow')
  const tUpload = useTranslations('upload')
  const inputRef = useRef<HTMLInputElement>(null)
  const [checking, setChecking] = useState(false)
  const [error, setError] = useState("")

  async function handlePick(picked: File | undefined) {
    if (!picked) return
    setError("")
    setChecking(true)
    try {
      const check = await checkInstrumentalFile(picked, null)
      if (!check.ok) {
        setError(check.reason === 'tooLarge'
          ? tUpload('tooLarge', { size: check.sizeMb, max: check.maxMb })
          : t('ownInstrumentalMismatch', {
              instrumental: formatDuration(check.fileSeconds),
              song: formatDuration(check.expectedSeconds),
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
