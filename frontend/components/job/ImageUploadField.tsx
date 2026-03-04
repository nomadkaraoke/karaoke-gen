"use client"

import { useCallback, useRef, useState } from "react"
import { Upload, X, ImageIcon } from "lucide-react"
import { Button } from "@/components/ui/button"

interface ImageUploadFieldProps {
  label: string
  description?: string
  file: File | null
  onChange: (file: File | null) => void
  disabled?: boolean
}

const MAX_SIZE_BYTES = 10 * 1024 * 1024 // 10MB
const ALLOWED_TYPES = ["image/png", "image/jpeg"]

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function ImageUploadField({ label, description, file, onChange, disabled }: ImageUploadFieldProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragOver, setDragOver] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const validate = useCallback((f: File): string | null => {
    if (!ALLOWED_TYPES.includes(f.type)) {
      return "Only PNG and JPG images are allowed."
    }
    if (f.size > MAX_SIZE_BYTES) {
      return `File too large (${formatFileSize(f.size)}). Maximum is 10 MB.`
    }
    return null
  }, [])

  const handleFile = useCallback((f: File) => {
    const err = validate(f)
    if (err) {
      setError(err)
      return
    }
    setError(null)
    onChange(f)
  }, [validate, onChange])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    if (disabled) return
    const f = e.dataTransfer.files[0]
    if (f) handleFile(f)
  }, [disabled, handleFile])

  const handleInputChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (f) handleFile(f)
    // Reset input so same file can be re-selected
    e.target.value = ""
  }, [handleFile])

  const previewUrl = file ? URL.createObjectURL(file) : null

  return (
    <div className="space-y-1.5">
      <label className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>
        {label}
      </label>
      {description && (
        <p className="text-[10px]" style={{ color: 'var(--text-muted)' }}>{description}</p>
      )}

      {file ? (
        <div className="relative rounded-lg overflow-hidden border" style={{ borderColor: 'var(--card-border)' }}>
          {/* 16:9 preview thumbnail */}
          {previewUrl && (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={previewUrl}
              alt="Preview"
              className="w-full object-cover"
              style={{ aspectRatio: "16/9" }}
              onLoad={() => URL.revokeObjectURL(previewUrl)}
            />
          )}
          {/* Info bar */}
          <div
            className="flex items-center justify-between px-3 py-1.5"
            style={{ backgroundColor: 'var(--secondary)' }}
          >
            <div className="flex items-center gap-2 min-w-0">
              <ImageIcon className="w-3.5 h-3.5 shrink-0" style={{ color: 'var(--text-muted)' }} />
              <span className="text-xs truncate" style={{ color: 'var(--text)' }}>{file.name}</span>
              <span className="text-[10px] shrink-0" style={{ color: 'var(--text-muted)' }}>
                {formatFileSize(file.size)}
              </span>
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onChange(null)}
              disabled={disabled}
              className="h-6 w-6 p-0"
              style={{ color: 'var(--text-muted)' }}
            >
              <X className="w-3.5 h-3.5" />
            </Button>
          </div>
        </div>
      ) : (
        <div
          onDragOver={(e) => { e.preventDefault(); if (!disabled) setDragOver(true) }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          onClick={() => !disabled && inputRef.current?.click()}
          className={`flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed p-4 cursor-pointer transition-colors ${
            dragOver ? 'border-[var(--brand-pink)]' : ''
          } ${disabled ? 'opacity-50 cursor-not-allowed' : 'hover:border-[var(--brand-pink)]'}`}
          style={{
            borderColor: dragOver ? 'var(--brand-pink)' : 'var(--card-border)',
            backgroundColor: dragOver ? 'rgba(255,122,204,0.05)' : 'transparent',
          }}
        >
          <Upload className="w-5 h-5" style={{ color: 'var(--text-muted)' }} />
          <p className="text-xs text-center" style={{ color: 'var(--text-muted)' }}>
            Drop an image here or <span style={{ color: 'var(--brand-pink)' }}>browse</span>
          </p>
          <p className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
            PNG or JPG, max 10 MB
          </p>
        </div>
      )}

      {error && (
        <p className="text-xs text-red-400">{error}</p>
      )}

      <input
        ref={inputRef}
        type="file"
        accept="image/png,image/jpeg"
        onChange={handleInputChange}
        className="hidden"
      />
    </div>
  )
}
