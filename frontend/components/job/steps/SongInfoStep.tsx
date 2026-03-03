"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Button } from "@/components/ui/button"
import { ArrowRight, Lightbulb, Pencil, Search, Upload, Youtube } from "lucide-react"
import { AutocompleteInput, AutocompleteSuggestion } from "@/components/ui/autocomplete-input"
import { CommunityVersionBanner } from "@/components/job/CommunityVersionBanner"
import { api, CommunityCheckResponse } from "@/lib/api"

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
  const canProceed = artist.trim() && title.trim()

  // Community version check state
  const [communityData, setCommunityData] = useState<CommunityCheckResponse | null>(null)
  const [communityDismissed, setCommunityDismissed] = useState(false)
  const communityCheckRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Track autocomplete — only active when artist field has a value
  const fetchTrackSuggestions = useCallback(async (query: string): Promise<AutocompleteSuggestion[]> => {
    if (!artist.trim()) return []
    try {
      const results = await api.searchCatalogTracks(query, artist.trim(), 10)
      return results.map((r) => {
        const mins = r.duration_ms ? Math.floor(r.duration_ms / 60000) : null
        const secs = r.duration_ms ? Math.floor((r.duration_ms % 60000) / 1000) : null
        const duration = mins !== null && secs !== null ? `${mins}:${secs.toString().padStart(2, "0")}` : ""
        return {
          key: r.track_id || `${r.artist_name}-${r.track_name}`,
          label: r.track_name,
          description: r.artist_name,
          meta: duration,
          data: r,
        }
      })
    } catch {
      return []
    }
  }, [artist])

  // Community version check — debounced when both fields are filled
  useEffect(() => {
    if (communityCheckRef.current) clearTimeout(communityCheckRef.current)
    setCommunityData(null)
    setCommunityDismissed(false)

    if (!artist.trim() || !title.trim()) return

    communityCheckRef.current = setTimeout(async () => {
      try {
        const data = await api.checkCommunityVersions(artist.trim(), title.trim())
        setCommunityData(data)
      } catch {
        // Silently fail — community check is non-blocking
      }
    }, 500)

    return () => {
      if (communityCheckRef.current) clearTimeout(communityCheckRef.current)
    }
  }, [artist, title])

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
          <span>We find the best audio</span>
        </div>
        <span className="opacity-40">or</span>
        <div className="flex items-center gap-1.5">
          <Upload className="w-3.5 h-3.5 text-amber-400" />
          <span>upload a file</span>
        </div>
        <span className="opacity-40">/</span>
        <div className="flex items-center gap-1.5">
          <Youtube className="w-3.5 h-3.5 text-red-400" />
          <span>YouTube URL</span>
        </div>
        <span className="opacity-50">(on next page)</span>
      </div>

      {/* Search artist/title */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="guided-artist" style={{ color: 'var(--text)' }}>Artist</Label>
          <Input
            id="guided-artist"
            data-testid="guided-artist-input"
            placeholder="e.g. Queen"
            value={artist}
            onChange={(e) => onArtistChange(e.target.value)}
            disabled={disabled}
            autoFocus
            style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="guided-title" style={{ color: 'var(--text)' }}>Title</Label>
          <AutocompleteInput
            id="guided-title"
            data-testid="guided-title-input"
            placeholder="e.g. Bohemian Rhapsody"
            value={title}
            onChange={onTitleChange}
            onSelect={(s) => {
              onTitleChange(s.label)
              // Overwrite artist with the canonical name from the selected track
              if (s.data?.artist_name) {
                onArtistChange(s.data.artist_name)
              }
            }}
            fetchSuggestions={fetchTrackSuggestions}
            disabled={disabled}
            style={{ backgroundColor: 'var(--secondary)', borderColor: 'var(--card-border)', color: 'var(--text)' }}
          />
        </div>
      </div>

      {/* Community version banner */}
      {communityData?.has_community && !communityDismissed && (
        <CommunityVersionBanner
          data={communityData}
          onDismiss={() => setCommunityDismissed(true)}
        />
      )}

      {/* Tip callout */}
      <div
        className="flex items-start gap-2.5 rounded-lg p-3 text-xs"
        style={{ backgroundColor: 'var(--secondary)', color: 'var(--text-muted)' }}
      >
        <Lightbulb className="w-4 h-4 shrink-0 mt-0.5 text-amber-400" />
        <p>
          Looking for a specific version (live, cover, remix)? Include those details in the title - e.g. &quot;Bohemian Rhapsody - Live at Wembley&quot;.
          <br className="mt-1" />
          If we can&apos;t find the right audio, you can provide a YouTube URL or upload a file on the next page.
        </p>
      </div>

      {/* Custom lyrics note */}
      <div
        className="flex items-start gap-2.5 rounded-lg p-3 text-xs"
        style={{ backgroundColor: 'var(--secondary)', color: 'var(--text-muted)' }}
      >
        <Pencil className="w-4 h-4 shrink-0 mt-0.5 text-purple-400" />
        <p>
          Making a video with custom lyrics? Start with the artist/title of the original song here - you can change the title screen and lyrics later.
        </p>
      </div>

      {/* Unreleased / own audio note */}
      <div
        className="flex items-start gap-2.5 rounded-lg p-3 text-xs"
        style={{ backgroundColor: 'var(--secondary)', color: 'var(--text-muted)' }}
      >
        <Upload className="w-4 h-4 shrink-0 mt-0.5 text-blue-400" />
        <p>
          Have an unreleased song or your own audio file? Enter the artist and title here anyway — you can upload your file on step 2, and mark it private on step 3 if you&apos;re not ready to publish it.
        </p>
      </div>

      <Button
        type="submit"
        disabled={!canProceed || disabled}
        className="w-full bg-[var(--brand-pink)] hover:bg-[var(--brand-pink-hover)] text-white shadow-[0_0_15px_rgba(255,122,204,0.3)] hover:shadow-[0_0_20px_rgba(255,122,204,0.5)]"
      >
        Choose Audio
        <ArrowRight className="w-4 h-4 ml-2" />
      </Button>
    </form>
  )
}
