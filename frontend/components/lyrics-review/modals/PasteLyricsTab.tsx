'use client'

import { useTranslations } from 'next-intl'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { Loader2 } from 'lucide-react'

interface PasteLyricsTabProps {
  onAdd: (source: string, lyrics: string) => Promise<void>
  onClose: () => void
  disabled?: boolean
}

export default function PasteLyricsTab({
  onAdd,
  onClose,
  disabled = false,
}: PasteLyricsTabProps) {
  const t = useTranslations('lyricsReview.modals.pasteLyrics')
  const [source, setSource] = useState('')
  const [lyrics, setLyrics] = useState('')
  const [isAdding, setIsAdding] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleAdd = async () => {
    if (!source.trim() || !lyrics.trim()) return

    setIsAdding(true)
    setError(null)

    try {
      await onAdd(source.trim(), lyrics.trim())
      setSource('')
      setLyrics('')
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to add lyrics')
    } finally {
      setIsAdding(false)
    }
  }

  const handleCancel = () => {
    if (!isAdding) {
      setSource('')
      setLyrics('')
      setError(null)
      onClose()
    }
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="paste-source">{t('sourceName')}</Label>
        <Input
          id="paste-source"
          value={source}
          onChange={(e) => setSource(e.target.value)}
          placeholder={t('sourceNamePlaceholder')}
          disabled={isAdding || disabled}
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="paste-lyrics">{t('lyrics')}</Label>
        <Textarea
          id="paste-lyrics"
          value={lyrics}
          onChange={(e) => setLyrics(e.target.value)}
          placeholder={t('lyricsPlaceholder')}
          rows={10}
          disabled={isAdding || disabled}
          className="font-mono text-sm"
        />
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      <div className="flex justify-end gap-2 pt-2">
        <Button variant="outline" onClick={handleCancel} disabled={isAdding || disabled}>
          Cancel
        </Button>
        <Button
          onClick={handleAdd}
          disabled={isAdding || disabled || !source.trim() || !lyrics.trim()}
        >
          {isAdding ? (
            <>
              <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              {t('adding')}
            </>
          ) : (
            t('addLyrics')
          )}
        </Button>
      </div>
    </div>
  )
}
