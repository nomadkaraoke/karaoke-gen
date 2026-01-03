"use client"

import { useEffect, useState, useRef, useCallback } from "react"
import { api } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Download, Loader2, ExternalLink, FolderOpen, Copy } from "lucide-react"

interface OutputLinksProps {
  jobId: string
}

export function OutputLinks({ jobId }: OutputLinksProps) {
  const [downloadUrls, setDownloadUrls] = useState<Record<string, any> | null>(null)
  const [youtubeUrl, setYoutubeUrl] = useState<string | null>(null)
  const [dropboxUrl, setDropboxUrl] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [copiedUrl, setCopiedUrl] = useState<string | null>(null)
  const copyTimeoutRef = useRef<NodeJS.Timeout | null>(null)

  useEffect(() => {
    loadOutputLinks()
  }, [jobId])

  // Cleanup timeout on unmount
  useEffect(() => {
    return () => {
      if (copyTimeoutRef.current) {
        clearTimeout(copyTimeoutRef.current)
      }
    }
  }, [])

  async function loadOutputLinks() {
    setIsLoading(true)
    try {
      const result = await api.getDownloadUrls(jobId)
      setDownloadUrls(result.download_urls)

      // Extract YouTube and Dropbox URLs from state_data if available
      const job = await api.getJob(jobId)
      if (job.state_data?.youtube_url) {
        setYoutubeUrl(job.state_data.youtube_url)
      }
      if (job.state_data?.dropbox_link) {
        setDropboxUrl(job.state_data.dropbox_link)
      }
    } catch (err) {
      console.error("Failed to load output links:", err)
    } finally {
      setIsLoading(false)
    }
  }

  const copyToClipboard = useCallback(async (url: string) => {
    // Check if clipboard API is available
    if (!navigator?.clipboard?.writeText) {
      console.error("Clipboard API not available")
      return
    }

    try {
      await navigator.clipboard.writeText(url)
      setCopiedUrl(url)

      // Clear any existing timeout
      if (copyTimeoutRef.current) {
        clearTimeout(copyTimeoutRef.current)
      }

      // Set new timeout
      copyTimeoutRef.current = setTimeout(() => {
        setCopiedUrl(null)
      }, 2000)
    } catch (err) {
      console.error("Failed to copy to clipboard:", err)
    }
  }, [])

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--text-muted)' }}>
        <Loader2 className="w-3 h-3 animate-spin" />
        Loading downloads...
      </div>
    )
  }

  const hasOutputs = youtubeUrl || dropboxUrl || (downloadUrls && Object.keys(downloadUrls).length > 0)

  return (
    <div className="space-y-2">
      {/* Combined Output Links */}
      {hasOutputs && (
        <div className="space-y-2">
          <p className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>Outputs:</p>
          <div className="flex flex-wrap gap-2">
            {/* Distribution Links */}
            {youtubeUrl && (
              <div className="flex gap-1">
                <a
                  href={youtubeUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-l bg-red-600 hover:bg-red-500 text-white"
                  onClick={(e) => e.stopPropagation()}
                >
                  <ExternalLink className="w-3 h-3" />
                  View on YouTube
                </a>
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); copyToClipboard(youtubeUrl) }}
                  className="inline-flex items-center text-xs px-2 py-1.5 rounded-r bg-red-700 hover:bg-red-600 text-white"
                  title="Copy YouTube URL"
                  aria-label={copiedUrl === youtubeUrl ? "YouTube URL copied" : "Copy YouTube URL"}
                >
                  {copiedUrl === youtubeUrl ? <span className="text-[10px]">Copied!</span> : <Copy className="w-3 h-3" />}
                </button>
              </div>
            )}
            {dropboxUrl && (
              <div className="flex gap-1">
                <a
                  href={dropboxUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-l bg-blue-600 hover:bg-blue-500 text-white"
                  onClick={(e) => e.stopPropagation()}
                >
                  <FolderOpen className="w-3 h-3" />
                  Dropbox Folder
                </a>
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); copyToClipboard(dropboxUrl) }}
                  className="inline-flex items-center text-xs px-2 py-1.5 rounded-r bg-blue-700 hover:bg-blue-600 text-white"
                  title="Copy Dropbox URL"
                  aria-label={copiedUrl === dropboxUrl ? "Dropbox URL copied" : "Copy Dropbox URL"}
                >
                  {copiedUrl === dropboxUrl ? <span className="text-[10px]">Copied!</span> : <Copy className="w-3 h-3" />}
                </button>
              </div>
            )}
            {/* Download Links */}
            {downloadUrls?.finals?.lossy_720p_mp4 && (
              <a
                href={api.getDownloadUrl(jobId, "finals", "lossy_720p_mp4")}
                className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded bg-green-600 hover:bg-green-500 text-white"
                onClick={(e) => e.stopPropagation()}
              >
                <Download className="w-3 h-3" />
                720p Video
              </a>
            )}
            {downloadUrls?.finals?.lossy_4k_mp4 && (
              <a
                href={api.getDownloadUrl(jobId, "finals", "lossy_4k_mp4")}
                className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded bg-green-600 hover:bg-green-500 text-white"
                onClick={(e) => e.stopPropagation()}
              >
                <Download className="w-3 h-3" />
                4K Video
              </a>
            )}
            {downloadUrls?.videos?.with_vocals && (
              <a
                href={api.getDownloadUrl(jobId, "videos", "with_vocals")}
                className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded bg-purple-600 hover:bg-purple-500 text-white"
                onClick={(e) => e.stopPropagation()}
              >
                <Download className="w-3 h-3" />
                With Vocals
              </a>
            )}
            {downloadUrls?.packages?.cdg_zip && (
              <a
                href={api.getDownloadUrl(jobId, "packages", "cdg_zip")}
                className="inline-flex items-center gap-1 text-xs px-2 py-1.5 rounded bg-gray-700 hover:bg-gray-600 text-white"
                onClick={(e) => e.stopPropagation()}
              >
                <Download className="w-3 h-3" />
                CDG+MP3
              </a>
            )}
            {downloadUrls?.packages?.txt_zip && (
              <a
                href={api.getDownloadUrl(jobId, "packages", "txt_zip")}
                className="inline-flex items-center gap-1 text-xs px-2 py-1.5 rounded bg-gray-700 hover:bg-gray-600 text-white"
                onClick={(e) => e.stopPropagation()}
              >
                <Download className="w-3 h-3" />
                TXT+MP3
              </a>
            )}
          </div>
        </div>
      )}

      {!hasOutputs && (
        <p className="text-xs" style={{ color: 'var(--text-muted)' }}>No outputs available yet</p>
      )}
    </div>
  )
}
