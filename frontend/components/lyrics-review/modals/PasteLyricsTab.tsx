'use client'

import { useTranslations } from 'next-intl'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Loader2, AlertTriangle } from 'lucide-react'
import { AddLyricsResult, RejectedSource } from '@/lib/lyrics-review/types'

interface PasteLyricsTabProps {
  onAdd: (source: string, lyrics: string, force?: boolean) => Promise<AddLyricsResult | void>
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
  const [rejection, setRejection] = useState<Partial<RejectedSource> | null>(null)

  const handleAdd = async (force = false) => {
    if (!source.trim() || !lyrics.trim()) return

    setIsAdding(true)
    setError(null)

    try {
      const result = await onAdd(source.trim(), lyrics.trim(), force)
      if (result && result.status === 'rejected') {
        // Keep the pasted text so the user can force-add or fix it
        setRejection(result.rejection ?? {})
        return
      }
      setRejection(null)
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
      setRejection(null)
      onClose()
    }
  }

  const rejectionPercent =
    rejection && typeof rejection.relevance === 'number'
      ? Math.round(rejection.relevance * 100)
      : 0

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="paste-source">{t('sourceName')}</Label>
        <Input
          id="paste-source"
          value={source}
          onChange={(e) => {
            setSource(e.target.value)
            setRejection(null)
          }}
          placeholder={t('sourceNamePlaceholder')}
          disabled={isAdding || disabled}
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="paste-lyrics">{t('lyrics')}</Label>
        <Textarea
          id="paste-lyrics"
          value={lyrics}
          onChange={(e) => {
            setLyrics(e.target.value)
            setRejection(null)
          }}
          placeholder={t('lyricsPlaceholder')}
          rows={10}
          disabled={isAdding || disabled}
          className="font-mono text-sm"
        />
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      {rejection && (
        <Alert variant="destructive">
          <AlertTriangle className="h-4 w-4" />
          <AlertDescription>
            {t('rejectedWarning', { percent: rejectionPercent })}
          </AlertDescription>
        </Alert>
      )}

      <div className="flex justify-end gap-2 pt-2">
        <Button variant="outline" onClick={handleCancel} disabled={isAdding || disabled}>
          Cancel
        </Button>
        {rejection && (
          <Button
            variant="destructive"
            onClick={() => handleAdd(true)}
            disabled={isAdding || disabled || !source.trim() || !lyrics.trim()}
          >
            {isAdding ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                {t('adding')}
              </>
            ) : (
              t('addAnyway')
            )}
          </Button>
        )}
        <Button
          onClick={() => handleAdd()}
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
