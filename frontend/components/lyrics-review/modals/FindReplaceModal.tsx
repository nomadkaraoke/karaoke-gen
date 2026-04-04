'use client'

import { useTranslations } from 'next-intl'
import { useState, useEffect } from 'react'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Loader2, AlertCircle } from 'lucide-react'
import { CorrectionData } from '@/lib/lyrics-review/types'

interface MatchPreview {
  segmentIndex: number
  wordIndex?: number
  segmentText: string
  wordText: string
  replacement: string
  willBeRemoved: boolean
}

interface FindReplaceModalProps {
  open: boolean
  onClose: () => void
  onReplace: (
    findText: string,
    replaceText: string,
    options: { caseSensitive: boolean; useRegex: boolean; fullTextMode: boolean }
  ) => void
  data: CorrectionData
}

export default function FindReplaceModal({
  open,
  onClose,
  onReplace,
  data,
}: FindReplaceModalProps) {
  const t = useTranslations('lyricsReview.modals.findReplace')
  const [findText, setFindText] = useState('')
  const [replaceText, setReplaceText] = useState('')
  const [caseSensitive, setCaseSensitive] = useState(false)
  const [useRegex, setUseRegex] = useState(false)
  const [fullTextMode, setFullTextMode] = useState(false)
  const [matchPreviews, setMatchPreviews] = useState<MatchPreview[]>([])
  const [regexError, setRegexError] = useState<string | null>(null)
  const [isSearching, setIsSearching] = useState(false)

  // Reset state when modal opens
  useEffect(() => {
    if (open) {
      setMatchPreviews([])
      setRegexError(null)
    }
  }, [open])

  // Find matches whenever search parameters change
  useEffect(() => {
    if (!open || !findText) {
      setMatchPreviews([])
      setRegexError(null)
      return
    }

    setIsSearching(true)

    const timeoutId = setTimeout(() => {
      try {
        const matches: MatchPreview[] = []
        const segments = data.corrected_segments || []

        let pattern: RegExp
        if (useRegex) {
          pattern = new RegExp(findText, caseSensitive ? 'g' : 'gi')
        } else {
          const escaped = findText.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
          pattern = new RegExp(escaped, caseSensitive ? 'g' : 'gi')
        }

        segments.forEach((segment, segmentIndex) => {
          segment.words.forEach((word, wordIndex) => {
            if (pattern.test(word.text)) {
              pattern.lastIndex = 0
              const replacedText = word.text.replace(pattern, replaceText)
              matches.push({
                segmentIndex,
                wordIndex,
                segmentText: segment.text,
                wordText: word.text,
                replacement: replacedText,
                willBeRemoved: replacedText.trim() === '',
              })
            }
          })
        })

        setMatchPreviews(matches)
        setRegexError(null)
      } catch (error) {
        setRegexError(error instanceof Error ? error.message : 'Invalid regex pattern')
        setMatchPreviews([])
      } finally {
        setIsSearching(false)
      }
    }, 300)

    return () => clearTimeout(timeoutId)
  }, [open, data, findText, replaceText, caseSensitive, useRegex, fullTextMode])

  const handleReplace = () => {
    if (!findText || regexError) return
    onReplace(findText, replaceText, { caseSensitive, useRegex, fullTextMode })
    onClose()
  }

  const handleKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'Enter' && !event.shiftKey && !regexError && findText) {
      event.preventDefault()
      handleReplace()
    }
  }

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <DialogContent className="max-w-2xl" onKeyDown={handleKeyDown}>
        <DialogHeader>
          <DialogTitle>{t('title')}</DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="find">{t('find')}</Label>
            <Input
              id="find"
              value={findText}
              onChange={(e) => setFindText(e.target.value)}
              placeholder={t('findPlaceholder')}
              autoFocus
              className={regexError ? 'border-destructive' : ''}
            />
            {regexError && <p className="text-sm text-destructive">{regexError}</p>}
          </div>

          <div className="space-y-2">
            <Label htmlFor="replace">{t('replaceWith')}</Label>
            <Input
              id="replace"
              value={replaceText}
              onChange={(e) => setReplaceText(e.target.value)}
              placeholder={t('replacePlaceholder')}
            />
          </div>

          <div className="flex flex-wrap gap-4">
            <div className="flex items-center space-x-2">
              <Switch
                id="caseSensitive"
                checked={caseSensitive}
                onCheckedChange={setCaseSensitive}
              />
              <Label htmlFor="caseSensitive">{t('caseSensitive')}</Label>
            </div>
            <div className="flex items-center space-x-2">
              <Switch id="useRegex" checked={useRegex} onCheckedChange={setUseRegex} />
              <Label htmlFor="useRegex">{t('useRegex')}</Label>
            </div>
            <div className="flex items-center space-x-2">
              <Switch
                id="fullTextMode"
                checked={fullTextMode}
                onCheckedChange={setFullTextMode}
              />
              <Label htmlFor="fullTextMode">{t('fullTextMode')}</Label>
            </div>
          </div>

          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <h4 className="font-medium">{t('preview')}</h4>
              {isSearching && <Loader2 className="h-4 w-4 animate-spin" />}
            </div>

            {matchPreviews.some((m) => m.willBeRemoved) && (
              <Alert variant="destructive">
                <AlertCircle className="h-4 w-4" />
                <AlertDescription>
                  {t('emptyWordsWarning')}
                </AlertDescription>
              </Alert>
            )}

            {!isSearching && findText && matchPreviews.length === 0 && !regexError && (
              <Alert>
                <AlertDescription>{t('noMatches')}</AlertDescription>
              </Alert>
            )}

            {!isSearching && matchPreviews.length > 0 && (
              <>
                <p className="text-sm text-muted-foreground">
                  {t('matchesFound', { count: matchPreviews.length })}
                </p>
                <ScrollArea className="h-48 border rounded-md p-2">
                  <div className="space-y-2">
                    {matchPreviews.slice(0, 50).map((preview, index) => (
                      <div key={index} className="text-sm p-2 bg-muted rounded">
                        <div>
                          <span className="text-destructive font-medium">{preview.wordText}</span>
                          <span className="text-muted-foreground"> → </span>
                          {preview.willBeRemoved ? (
                            <span className="text-yellow-600 font-medium">(will be removed)</span>
                          ) : (
                            <span className="text-primary font-medium">{preview.replacement}</span>
                          )}
                        </div>
                        <div className="text-xs text-muted-foreground mt-1">
                          Segment {preview.segmentIndex + 1}
                        </div>
                      </div>
                    ))}
                    {matchPreviews.length > 50 && (
                      <p className="text-sm text-muted-foreground">
                        {matchPreviews.length - 50} more matches not shown
                      </p>
                    )}
                  </div>
                </ScrollArea>
              </>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={handleReplace}
            disabled={!findText || !!regexError || matchPreviews.length === 0}
          >
            {t('replaceAll', { count: matchPreviews.length })}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
