"use client"

import { useState, useEffect } from "react"
import { useTranslations } from 'next-intl'
import { api } from "@/lib/api"
import { useTenant } from "@/lib/tenant"
import type { VideoThemeSummary } from "@/lib/video-themes"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Button } from "@/components/ui/button"
import { Palette, AlertCircle, Eye, EyeOff, Lock } from "lucide-react"

interface ThemeSelectorProps {
  value?: string
  onChange: (themeId: string | undefined) => void
  disabled?: boolean
}

export function ThemeSelector({ value, onChange, disabled }: ThemeSelectorProps) {
  const t = useTranslations('themeSelector')
  const { defaults, isDefault: isDefaultTenant } = useTenant()
  const [themes, setThemes] = useState<VideoThemeSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showPreview, setShowPreview] = useState(false)

  // Check if theme is locked by tenant config
  const lockedTheme = defaults.locked_theme

  useEffect(() => {
    async function loadThemes() {
      try {
        setLoading(true)
        setError(null)
        const themeList = await api.listThemes()
        setThemes(themeList)

        // If theme is locked by tenant, always use that
        if (lockedTheme) {
          onChange(lockedTheme)
          return
        }

        // If no value is set and there's a default theme, select it
        if (!value && themeList.length > 0) {
          const defaultTheme = themeList.find(t => t.is_default)
          if (defaultTheme) {
            onChange(defaultTheme.id)
          } else {
            onChange(themeList[0].id)
          }
        }
      } catch (err) {
        console.error("Failed to load themes:", err)
        setError(t('failedToLoadThemes'))
      } finally {
        setLoading(false)
      }
    }

    loadThemes()
  }, [lockedTheme]) // Re-run if locked theme changes

  const selectedTheme = themes.find(t => t.id === value)

  if (loading) {
    return (
      <div className="space-y-2">
        <Label className="flex items-center gap-2" style={{ color: 'var(--text)' }}>
          <Palette className="w-4 h-4" />
          {t('videoTheme')}
        </Label>
        <Skeleton className="h-10 w-full" style={{ backgroundColor: 'var(--secondary)' }} />
      </div>
    )
  }

  if (error || themes.length === 0) {
    return (
      <div className="space-y-2">
        <Label className="flex items-center gap-2" style={{ color: 'var(--text)' }}>
          <Palette className="w-4 h-4" />
          {t('videoTheme')}
        </Label>
        <div className="flex items-center gap-2 text-sm text-amber-400 bg-amber-500/10 rounded p-2">
          <AlertCircle className="w-4 h-4" />
          {error || t('noThemesAvailable')}
        </div>
      </div>
    )
  }

  // If theme is locked by tenant, show read-only display
  if (lockedTheme && selectedTheme) {
    return (
      <div className="space-y-2">
        <Label className="flex items-center gap-2" style={{ color: 'var(--text)' }}>
          <Palette className="w-4 h-4" />
          {t('videoTheme')}
        </Label>
        <div
          className="flex items-center gap-2 px-3 py-2 rounded-md border"
          style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
        >
          <Lock className="w-4 h-4" style={{ color: 'var(--text-muted)' }} />
          <span>{selectedTheme.name}</span>
        </div>
        {/* Optional: Show theme preview for locked themes */}
        {selectedTheme.thumbnail_url && (
          <div className="mt-2 relative w-full aspect-video max-w-xs rounded-lg overflow-hidden border" style={{ borderColor: 'var(--card-border)' }}>
            <img
              src={selectedTheme.thumbnail_url}
              alt={`${selectedTheme.name} preview`}
              className="w-full h-full object-cover"
            />
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <Label htmlFor="theme-select" className="flex items-center gap-2" style={{ color: 'var(--text)' }}>
        <Palette className="w-4 h-4" />
        Video Theme
      </Label>

      <Select
        value={value || ""}
        onValueChange={(val) => onChange(val || undefined)}
        disabled={disabled || !!lockedTheme}
      >
        <SelectTrigger
          id="theme-select"
          style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
        >
          <SelectValue placeholder={t('selectATheme')} />
        </SelectTrigger>
        <SelectContent style={{ backgroundColor: 'var(--card)', borderColor: 'var(--card-border)' }}>
          {themes.map((theme) => (
            <SelectItem
              key={theme.id}
              value={theme.id}
              style={{ color: 'var(--text)' }}
            >
              <div className="flex items-center gap-2">
                <span>{theme.name}</span>
                {theme.is_default && (
                  <span className="text-xs text-amber-400">{t('default')}</span>
                )}
              </div>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {/* Theme preview toggle */}
      {selectedTheme?.thumbnail_url && (
        <div className="mt-2 space-y-2">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setShowPreview(!showPreview)}
            className="px-0"
            style={{ color: 'var(--text-muted)' }}
          >
            {showPreview ? (
              <><EyeOff className="w-4 h-4 mr-1.5" />{t('hidePreview')}</>
            ) : (
              <><Eye className="w-4 h-4 mr-1.5" />{t('showPreview')}</>
            )}
          </Button>

          {showPreview && (
            <div className="relative w-full aspect-video max-w-xs rounded-lg overflow-hidden border" style={{ borderColor: 'var(--card-border)' }}>
              <img
                src={selectedTheme.thumbnail_url}
                alt={`${selectedTheme.name} preview`}
                className="w-full h-full object-cover"
              />
            </div>
          )}
        </div>
      )}
    </div>
  )
}
