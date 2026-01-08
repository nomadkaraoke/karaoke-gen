"use client"

import { useEffect, useState, useRef, useCallback } from "react"
import { api, adminApi } from "@/lib/api"
import { useTenant } from "@/lib/tenant"
import { Button } from "@/components/ui/button"
import { Download, Loader2, ExternalLink, FolderOpen, Copy, Mail, MessageSquare } from "lucide-react"
import { useAuth } from "@/lib/auth"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

interface OutputLinksProps {
  jobId: string
}

export function OutputLinks({ jobId }: OutputLinksProps) {
  const [downloadUrls, setDownloadUrls] = useState<Record<string, any> | null>(null)
  const [youtubeUrl, setYoutubeUrl] = useState<string | null>(null)
  const [dropboxUrl, setDropboxUrl] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [copiedUrl, setCopiedUrl] = useState<string | null>(null)
  const [copiedMessage, setCopiedMessage] = useState(false)
  const [isLoadingMessage, setIsLoadingMessage] = useState(false)
  const [showEmailDialog, setShowEmailDialog] = useState(false)
  const [emailInput, setEmailInput] = useState("")
  const [isSendingEmail, setIsSendingEmail] = useState(false)
  const [emailSent, setEmailSent] = useState(false)
  const copyTimeoutRef = useRef<NodeJS.Timeout | null>(null)
  const messageTimeoutRef = useRef<NodeJS.Timeout | null>(null)
  const emailTimeoutRef = useRef<NodeJS.Timeout | null>(null)

  const { user } = useAuth()
  const { features } = useTenant()
  const isAdmin = user?.role === 'admin'

  // Filter external links based on tenant features
  const showYoutubeLink = features.youtube_upload && youtubeUrl
  const showDropboxLink = features.dropbox_upload && dropboxUrl

  useEffect(() => {
    loadOutputLinks()
  }, [jobId])

  // Cleanup timeouts on unmount
  useEffect(() => {
    return () => {
      if (copyTimeoutRef.current) {
        clearTimeout(copyTimeoutRef.current)
      }
      if (messageTimeoutRef.current) {
        clearTimeout(messageTimeoutRef.current)
      }
      if (emailTimeoutRef.current) {
        clearTimeout(emailTimeoutRef.current)
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

  const copyCompletionMessage = useCallback(async () => {
    if (!navigator?.clipboard?.writeText) {
      console.error("Clipboard API not available")
      return
    }

    setIsLoadingMessage(true)
    try {
      const response = await adminApi.getCompletionMessage(jobId)
      await navigator.clipboard.writeText(response.message)
      setCopiedMessage(true)

      // Clear any existing timeout
      if (messageTimeoutRef.current) {
        clearTimeout(messageTimeoutRef.current)
      }

      // Set new timeout
      messageTimeoutRef.current = setTimeout(() => {
        setCopiedMessage(false)
      }, 2000)
    } catch (err) {
      console.error("Failed to copy completion message:", err)
    } finally {
      setIsLoadingMessage(false)
    }
  }, [jobId])

  const handleSendEmail = useCallback(async () => {
    if (!emailInput.trim()) return

    setIsSendingEmail(true)
    try {
      await adminApi.sendCompletionEmail(jobId, emailInput.trim(), true)
      setEmailSent(true)

      // Clear any existing timeout
      if (emailTimeoutRef.current) {
        clearTimeout(emailTimeoutRef.current)
      }
      // Set new timeout to close dialog after success message
      emailTimeoutRef.current = setTimeout(() => {
        setShowEmailDialog(false)
        setEmailSent(false)
        setEmailInput("")
      }, 1500)
    } catch (err) {
      console.error("Failed to send email:", err)
      alert("Failed to send email. Please check the email address and try again.")
    } finally {
      setIsSendingEmail(false)
    }
  }, [jobId, emailInput])

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--text-muted)' }}>
        <Loader2 className="w-3 h-3 animate-spin" />
        Loading downloads...
      </div>
    )
  }

  const hasOutputs = showYoutubeLink || showDropboxLink || (downloadUrls && Object.keys(downloadUrls).length > 0)

  // Check if we have any downloads
  const hasDownloads = downloadUrls && (
    downloadUrls?.finals?.lossy_720p_mp4 ||
    downloadUrls?.finals?.lossy_4k_mp4 ||
    downloadUrls?.videos?.with_vocals ||
    downloadUrls?.packages?.cdg_zip ||
    downloadUrls?.packages?.txt_zip
  )

  // Check if we have any external links (filtered by tenant features)
  const hasExternalLinks = showYoutubeLink || showDropboxLink

  return (
    <div className="space-y-3">
      {/* Downloads Section */}
      {hasDownloads && (
        <div className="space-y-1.5">
          <p className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>Downloads</p>
          <div className="flex flex-wrap gap-1.5">
            {downloadUrls?.finals?.lossy_4k_mp4 && (
              <a
                href={api.getDownloadUrl(jobId, "finals", "lossy_4k_mp4")}
                className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded bg-[var(--brand-pink)] hover:bg-[var(--brand-pink-hover)] text-white transition-colors"
                onClick={(e) => e.stopPropagation()}
              >
                <Download className="w-3 h-3" />
                4K Video
              </a>
            )}
            {downloadUrls?.finals?.lossy_720p_mp4 && (
              <a
                href={api.getDownloadUrl(jobId, "finals", "lossy_720p_mp4")}
                className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded bg-[#252525] hover:bg-[#333333] text-[var(--text)] border border-[var(--card-border)] transition-colors"
                onClick={(e) => e.stopPropagation()}
              >
                <Download className="w-3 h-3" />
                720p Video
              </a>
            )}
            {downloadUrls?.videos?.with_vocals && (
              <a
                href={api.getDownloadUrl(jobId, "videos", "with_vocals")}
                className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded bg-[#252525] hover:bg-[#333333] text-[var(--text)] border border-[var(--card-border)] transition-colors"
                onClick={(e) => e.stopPropagation()}
              >
                <Download className="w-3 h-3" />
                With Vocals
              </a>
            )}
            {downloadUrls?.packages?.cdg_zip && (
              <a
                href={api.getDownloadUrl(jobId, "packages", "cdg_zip")}
                className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded bg-[#252525] hover:bg-[#333333] text-[var(--text)] border border-[var(--card-border)] transition-colors"
                onClick={(e) => e.stopPropagation()}
              >
                <Download className="w-3 h-3" />
                CDG+MP3
              </a>
            )}
            {downloadUrls?.packages?.txt_zip && (
              <a
                href={api.getDownloadUrl(jobId, "packages", "txt_zip")}
                className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded bg-[#252525] hover:bg-[#333333] text-[var(--text)] border border-[var(--card-border)] transition-colors"
                onClick={(e) => e.stopPropagation()}
              >
                <Download className="w-3 h-3" />
                TXT+MP3
              </a>
            )}
          </div>
        </div>
      )}

      {/* Actions Section - only show links enabled for tenant */}
      {hasExternalLinks && (
        <div className="space-y-1.5">
          <p className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>Links</p>
          <div className="flex flex-wrap gap-1.5">
            {showYoutubeLink && youtubeUrl && (
              <div className="flex">
                <a
                  href={youtubeUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-l bg-red-600 hover:bg-red-500 text-white transition-colors"
                  onClick={(e) => e.stopPropagation()}
                >
                  <ExternalLink className="w-3 h-3" />
                  YouTube
                </a>
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); copyToClipboard(youtubeUrl) }}
                  className="inline-flex items-center text-xs px-2 py-1.5 rounded-r bg-red-700 hover:bg-red-600 text-white transition-colors"
                  title="Copy YouTube URL"
                  aria-label={copiedUrl === youtubeUrl ? "YouTube URL copied" : "Copy YouTube URL"}
                >
                  {copiedUrl === youtubeUrl ? <span className="text-[10px]">Copied!</span> : <Copy className="w-3 h-3" />}
                </button>
              </div>
            )}
            {showDropboxLink && dropboxUrl && (
              <div className="flex">
                <a
                  href={dropboxUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-l bg-[#0061FE] hover:bg-[#0052d6] text-white transition-colors"
                  onClick={(e) => e.stopPropagation()}
                >
                  <FolderOpen className="w-3 h-3" />
                  Dropbox
                </a>
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); copyToClipboard(dropboxUrl) }}
                  className="inline-flex items-center text-xs px-2 py-1.5 rounded-r bg-[#0052d6] hover:bg-[#0047ba] text-white transition-colors"
                  title="Copy Dropbox URL"
                  aria-label={copiedUrl === dropboxUrl ? "Dropbox URL copied" : "Copy Dropbox URL"}
                >
                  {copiedUrl === dropboxUrl ? <span className="text-[10px]">Copied!</span> : <Copy className="w-3 h-3" />}
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Admin-only tools */}
      {isAdmin && hasOutputs && (
        <div className="space-y-1.5 pt-2 border-t border-[var(--card-border)]">
          <p className="text-xs font-medium" style={{ color: 'var(--text-muted)' }}>Admin Tools</p>
          <div className="flex flex-wrap gap-1.5">
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); copyCompletionMessage() }}
              disabled={isLoadingMessage}
              className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded bg-[#252525] hover:bg-[#333333] text-[var(--text)] border border-[var(--card-border)] disabled:opacity-50 transition-colors"
              title="Copy completion message to clipboard"
            >
              {isLoadingMessage ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : (
                <MessageSquare className="w-3 h-3" />
              )}
              {copiedMessage ? "Copied!" : "Copy Message"}
            </button>
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); setShowEmailDialog(true) }}
              className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded bg-[#252525] hover:bg-[#333333] text-[var(--text)] border border-[var(--card-border)] transition-colors"
              title="Send completion email"
            >
              <Mail className="w-3 h-3" />
              Send Email
            </button>
          </div>
        </div>
      )}

      {!hasOutputs && (
        <p className="text-xs" style={{ color: 'var(--text-muted)' }}>No outputs available yet</p>
      )}

      {/* Send Email Dialog */}
      <Dialog open={showEmailDialog} onOpenChange={setShowEmailDialog}>
        <DialogContent className="sm:max-w-[425px]" onClick={(e) => e.stopPropagation()}>
          <DialogHeader>
            <DialogTitle>Send Completion Email</DialogTitle>
            <DialogDescription>
              Send the job completion message to an email address. The email will be CC'd to gen@nomadkaraoke.com.
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-4">
            <div className="grid grid-cols-4 items-center gap-4">
              <Label htmlFor="email" className="text-right">
                Email
              </Label>
              <Input
                id="email"
                type="email"
                value={emailInput}
                onChange={(e) => setEmailInput(e.target.value)}
                placeholder="customer@example.com"
                className="col-span-3"
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    handleSendEmail()
                  }
                }}
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setShowEmailDialog(false)}
              disabled={isSendingEmail}
            >
              Cancel
            </Button>
            <Button
              type="button"
              onClick={handleSendEmail}
              disabled={isSendingEmail || !emailInput.trim() || emailSent}
            >
              {isSendingEmail ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  Sending...
                </>
              ) : emailSent ? (
                "Sent!"
              ) : (
                "Send Email"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
