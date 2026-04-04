'use client'

import { useTranslations } from 'next-intl'
import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Checkbox } from '@/components/ui/checkbox'
import { Spinner } from '@/components/ui/spinner'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { SearchLyricsResponse, RejectedSource } from '@/lib/lyrics-review/types'

interface SearchLyricsTabProps {
  defaultArtist: string
  defaultTitle: string
  onSearch: (artist: string, title: string, forceSources: string[]) => Promise<SearchLyricsResponse>
  onClose: () => void
  disabled?: boolean
}

export default function SearchLyricsTab({
  defaultArtist,
  defaultTitle,
  onSearch,
  onClose,
  disabled = false,
}: SearchLyricsTabProps) {
  const t = useTranslations('lyricsReview.modals.searchLyrics')
  const [artist, setArtist] = useState(defaultArtist)
  const [title, setTitle] = useState(defaultTitle)
  const [isSearching, setIsSearching] = useState(false)
  const [searchResult, setSearchResult] = useState<SearchLyricsResponse | null>(null)
  const [selectedForce, setSelectedForce] = useState<Set<string>>(new Set())
  const [error, setError] = useState<string | null>(null)

  const handleSearch = async (forceSources: string[] = []) => {
    if (!artist.trim() || !title.trim()) return

    setIsSearching(true)
    setError(null)

    try {
      const result = await onSearch(artist.trim(), title.trim(), forceSources)
      if (result.status === 'success') {
        // Success: parent handles closing and updating data
        onClose()
      } else {
        // No results: store result to show rejected sources
        setSearchResult(result)
        setSelectedForce(new Set())
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Search failed')
    } finally {
      setIsSearching(false)
    }
  }

  const handleForceAdd = async () => {
    await handleSearch(Array.from(selectedForce))
  }

  const toggleForceSource = (source: string) => {
    setSelectedForce((prev) => {
      const next = new Set(prev)
      if (next.has(source)) {
        next.delete(source)
      } else {
        next.add(source)
      }
      return next
    })
  }

  const rejectedEntries = searchResult
    ? Object.entries(searchResult.sources_rejected ?? {})
    : []
  const notFoundSources = searchResult?.sources_not_found ?? []

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="search-artist">{t('artist')}</Label>
        <Input
          id="search-artist"
          value={artist}
          onChange={(e) => setArtist(e.target.value)}
          placeholder={t('artistPlaceholder')}
          disabled={isSearching || disabled}
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="search-title">{t('title')}</Label>
        <Input
          id="search-title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder={t('titlePlaceholder')}
          disabled={isSearching || disabled}
        />
      </div>

      <p className="text-xs text-muted-foreground">
        {t('searchDesc')}
      </p>

      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {searchResult && searchResult.status === 'no_results' && (
        <div className="space-y-3">
          <Alert>
            <AlertDescription>
              {searchResult.message || t('noMatches')}
            </AlertDescription>
          </Alert>

          {notFoundSources.length > 0 && (
            <p className="text-xs text-muted-foreground">
              {t('notFound', { sources: notFoundSources.join(', ') })}
            </p>
          )}

          {rejectedEntries.length > 0 && (
            <div className="space-y-2">
              <p className="text-sm font-medium">
                {t('lowConfidence')}
              </p>
              {rejectedEntries.map(([source, info]: [string, RejectedSource]) => (
                <div
                  key={source}
                  className="flex items-start gap-3 rounded-md border p-3 text-sm"
                >
                  <Checkbox
                    id={`force-${source}`}
                    checked={selectedForce.has(source)}
                    onCheckedChange={() => toggleForceSource(source)}
                    disabled={isSearching || disabled}
                  />
                  <div className="flex-1 space-y-1">
                    <label
                      htmlFor={`force-${source}`}
                      className="cursor-pointer font-medium"
                    >
                      {source}
                    </label>
                    <p className="text-muted-foreground">
                      &ldquo;{info.track_name}&rdquo; by {info.artist_names}
                    </p>
                    <p className="text-muted-foreground">
                      {t('matched', { matched: info.matched_words, total: info.total_words, relevance: Math.round(info.relevance * 100) })}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="flex justify-end gap-2 pt-2">
        {searchResult && searchResult.status === 'no_results' ? (
          <>
            <Button
              variant="outline"
              onClick={() => handleSearch()}
              disabled={isSearching || disabled || !artist.trim() || !title.trim()}
            >
              {isSearching ? (
                <>
                  <Spinner className="mr-2 h-4 w-4" />
                  {t('searching')}
                </>
              ) : (
                t('searchAgain')
              )}
            </Button>
            {rejectedEntries.length > 0 && (
              <Button
                onClick={handleForceAdd}
                disabled={isSearching || disabled || selectedForce.size === 0}
              >
                {isSearching ? (
                  <>
                    <Spinner className="mr-2 h-4 w-4" />
                    Adding...
                  </>
                ) : (
                  t('addSelected', { count: selectedForce.size })
                )}
              </Button>
            )}
          </>
        ) : (
          <Button
            onClick={() => handleSearch()}
            disabled={isSearching || disabled || !artist.trim() || !title.trim()}
          >
            {isSearching ? (
              <>
                <Spinner className="mr-2 h-4 w-4" />
                {t('searching')}
              </>
            ) : (
              t('searchAll')
            )}
          </Button>
        )}
      </div>
    </div>
  )
}
