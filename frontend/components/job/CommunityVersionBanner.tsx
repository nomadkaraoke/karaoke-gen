"use client"

import { X, ExternalLink } from "lucide-react"
import type { CommunityCheckResponse } from "@/lib/api"

interface CommunityVersionBannerProps {
  data: CommunityCheckResponse
  onDismiss: () => void
}

export function CommunityVersionBanner({ data, onDismiss }: CommunityVersionBannerProps) {
  if (!data.has_community || data.songs.length === 0) return null

  // Collect all community tracks across songs, take top 3
  const allTracks = data.songs.flatMap((song) =>
    song.community_tracks.map((track) => ({
      ...track,
      songArtist: song.artist,
      songTitle: song.title,
    }))
  )
  const displayTracks = allTracks.slice(0, 3)

  return (
    <div
      className="relative rounded-lg p-3 border"
      style={{
        borderColor: "rgba(34, 197, 94, 0.4)",
        backgroundColor: "rgba(34, 197, 94, 0.06)",
      }}
    >
      <button
        type="button"
        onClick={onDismiss}
        className="absolute top-2 right-2 p-1 rounded hover:bg-black/10 transition-colors"
        style={{ color: "var(--text-muted)" }}
        aria-label="Dismiss"
      >
        <X className="w-3.5 h-3.5" />
      </button>

      <div className="pr-6">
        <p className="text-sm font-medium" style={{ color: "rgb(34, 197, 94)" }}>
          A karaoke version of this song already exists!
        </p>
        <p className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>
          Check if an existing version works for you before using a credit.
        </p>

        <div className="mt-2 space-y-1.5">
          {displayTracks.map((track, i) => (
            <a
              key={i}
              href={track.youtube_url}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-2 text-xs rounded px-2 py-1.5 transition-colors hover:bg-black/5"
              style={{ color: "var(--text)" }}
            >
              <ExternalLink className="w-3.5 h-3.5 shrink-0" style={{ color: "rgb(34, 197, 94)" }} />
              <span className="truncate">
                {track.brand_name}
                {track.brand_code && (
                  <span
                    className="ml-1.5 px-1.5 py-0.5 rounded text-[10px] font-medium"
                    style={{
                      backgroundColor: "rgba(34, 197, 94, 0.15)",
                      color: "rgb(34, 197, 94)",
                    }}
                  >
                    Community
                  </span>
                )}
              </span>
              <span className="ml-auto shrink-0 text-[10px]" style={{ color: "var(--text-muted)" }}>
                Watch on YouTube
              </span>
            </a>
          ))}
        </div>

        {allTracks.length > 3 && (
          <p className="text-[10px] mt-1.5" style={{ color: "var(--text-muted)" }}>
            +{allTracks.length - 3} more version{allTracks.length - 3 > 1 ? "s" : ""}
          </p>
        )}
      </div>
    </div>
  )
}
