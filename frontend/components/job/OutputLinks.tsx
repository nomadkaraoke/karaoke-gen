"use client"

import { useRef, useCallback, useEffect, useState } from "react"
import { useTranslations } from 'next-intl'
import { api, adminApi, Job } from "@/lib/api"
import { useTenant } from "@/lib/tenant"
import { Button } from "@/components/ui/button"
import { Download, Loader2, ExternalLink, FolderOpen, Copy, Mail, Settings, Lock, Globe, Pencil } from "lucide-react"
import { useAuth } from "@/lib/auth"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { EditTrackModal } from "./EditTrackModal"

interface OutputLinksProps {
  job: Job
  onJobUpdated?: () => void
}

export function OutputLinks({ job, onJobUpdated }: OutputLinksProps) {
  const t = useTranslations('outputLinks')
  const tc = useTranslations('common')
  const tCard = useTranslations('jobCard')
  // Use file_urls from job directly - no API call needed
  const downloadUrls = job.file_urls || null
  // Use state_data from job directly - no API call needed
  const youtubeUrl = job.state_data?.youtube_url || null
  const dropboxUrl = job.state_data?.dropbox_link || null

  const [copiedUrl, setCopiedUrl] = useState<string | null>(null)
  const [copiedMessage, setCopiedMessage] = useState(false)
  const [isLoadingMessage, setIsLoadingMessage] = useState(false)
  const [showEmailDialog, setShowEmailDialog] = useState(false)
  const [emailInput, setEmailInput] = useState("")
  const [isSendingEmail, setIsSendingEmail] = useState(false)
  const [emailSent, setEmailSent] = useState(false)
  const [showVisibilityDialog, setShowVisibilityDialog] = useState(false)
  const [isChangingVisibility, setIsChangingVisibility] = useState(false)
  const [showEditModal, setShowEditModal] = useState(false)
  const copyTimeoutRef = useRef<NodeJS.Timeout | null>(null)
  const messageTimeoutRef = useRef<NodeJS.Timeout | null>(null)
  const emailTimeoutRef = useRef<NodeJS.Timeout | null>(null)

  // Cleanup timeouts on unmount
  useEffect(() => {
    return () => {
      if (copyTimeoutRef.current) clearTimeout(copyTimeoutRef.current)
      if (messageTimeoutRef.current) clearTimeout(messageTimeoutRef.current)
      if (emailTimeoutRef.current) clearTimeout(emailTimeoutRef.current)
    }
  }, [])

  const { user } = useAuth()
  const { features, tenantId } = useTenant()
  const isAdmin = user?.role === 'admin'

  // Check if outputs have been deleted by admin
  const outputsDeleted = !!job.outputs_deleted_at

  // Filter external links based on tenant features (and outputs not deleted)
  const showYoutubeLink = features.youtube_upload && youtubeUrl && !outputsDeleted
  const showDropboxLink = features.dropbox_upload && dropboxUrl && !outputsDeleted

  // Visibility change: available for completed consumer jobs (not tenant)
  const isPrivate = !!job.is_private
  const visibilityChangeInProgress = !!job.state_data?.visibility_change_in_progress
  const canChangeVisibility = job.status === "complete" && !tenantId && !visibilityChangeInProgress

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
      const response = await adminApi.getCompletionMessage(job.job_id)
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
  }, [job.job_id])

  const handleSendEmail = useCallback(async () => {
    if (!emailInput.trim()) return

    setIsSendingEmail(true)
    try {
      await adminApi.sendCompletionEmail(job.job_id, emailInput.trim(), true)
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
  }, [job.job_id, emailInput])

  const handleChangeVisibility = useCallback(async () => {
    const targetVisibility = isPrivate ? 'public' : 'private'
    setIsChangingVisibility(true)
    setShowVisibilityDialog(false)

    try {
      await api.changeVisibility(job.job_id, targetVisibility)
      onJobUpdated?.()
    } catch (err) {
      console.error("Failed to change visibility:", err)
      alert(`Failed to change visibility. ${err instanceof Error ? err.message : 'Please try again.'}`)
    } finally {
      setIsChangingVisibility(false)
    }
  }, [job.job_id, isPrivate, onJobUpdated])

  const hasOutputs = showYoutubeLink || showDropboxLink || (downloadUrls && Object.keys(downloadUrls).length > 0)

  // Check if we have any downloads (and outputs haven't been deleted)
  const hasDownloads = !outputsDeleted && downloadUrls && (
    downloadUrls?.finals?.lossy_720p_mp4 ||
    downloadUrls?.finals?.lossy_4k_mp4 ||
    downloadUrls?.finals?.with_vocals_mp4 ||
    downloadUrls?.videos?.with_vocals ||
    downloadUrls?.packages?.cdg_zip ||
    downloadUrls?.packages?.txt_zip
  )

  // Check if we have any external links (filtered by tenant features)
  const hasExternalLinks = showYoutubeLink || showDropboxLink

  return (
    <div className="space-y-2">
      {/* Links, Downloads, and Admin Tools - all in one row */}
      {(hasDownloads || hasExternalLinks || isAdmin || canChangeVisibility) && (
        <div className="flex flex-wrap gap-1.5">
          {/* Links first */}
          {hasExternalLinks && (
            <>
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
                      {t('youtube')}
                    </a>
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); copyToClipboard(youtubeUrl) }}
                      className="inline-flex items-center text-xs px-2 py-1.5 rounded-r bg-red-700 hover:bg-red-600 text-white transition-colors"
                      title={t('copyYoutubeUrl')}
                      aria-label={copiedUrl === youtubeUrl ? t('youtubeUrlCopied') : t('copyYoutubeUrl')}
                    >
                      {copiedUrl === youtubeUrl ? <span className="text-[10px]">{t('copied')}</span> : <Copy className="w-3 h-3" />}
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
                      {t('dropbox')}
                    </a>
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); copyToClipboard(dropboxUrl) }}
                      className="inline-flex items-center text-xs px-2 py-1.5 rounded-r bg-[#0052d6] hover:bg-[#0047ba] text-white transition-colors"
                      title={t('copyDropboxUrl')}
                      aria-label={copiedUrl === dropboxUrl ? t('dropboxUrlCopied') : t('copyDropboxUrl')}
                    >
                      {copiedUrl === dropboxUrl ? <span className="text-[10px]">{t('copied')}</span> : <Copy className="w-3 h-3" />}
                    </button>
                  </div>
                )}
            </>
          )}

          {/* Downloads */}
          {hasDownloads && (
            <>
                {downloadUrls?.finals?.lossy_4k_mp4 && (
                  <a
                    href={api.getDownloadUrl(job.job_id, "finals", "lossy_4k_mp4")}
                    className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded bg-[var(--brand-pink)] hover:bg-[var(--brand-pink-hover)] text-white transition-colors"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <Download className="w-3 h-3" />
                    {t('video4k')}
                  </a>
                )}
                {downloadUrls?.finals?.lossy_720p_mp4 && (
                  <a
                    href={api.getDownloadUrl(job.job_id, "finals", "lossy_720p_mp4")}
                    className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded bg-[#252525] hover:bg-[#333333] text-[var(--text)] border border-[var(--card-border)] transition-colors"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <Download className="w-3 h-3" />
                    {t('video720p')}
                  </a>
                )}
                {(downloadUrls?.finals?.with_vocals_mp4 || downloadUrls?.videos?.with_vocals) && (
                  <a
                    href={api.getDownloadUrl(job.job_id,
                      downloadUrls?.finals?.with_vocals_mp4 ? "finals" : "videos",
                      downloadUrls?.finals?.with_vocals_mp4 ? "with_vocals_mp4" : "with_vocals"
                    )}
                    className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded bg-[#252525] hover:bg-[#333333] text-[var(--text)] border border-[var(--card-border)] transition-colors"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <Download className="w-3 h-3" />
                    {t('withVocals')}
                  </a>
                )}
                {downloadUrls?.packages?.cdg_zip && (
                  <a
                    href={api.getDownloadUrl(job.job_id, "packages", "cdg_zip")}
                    className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded bg-[#252525] hover:bg-[#333333] text-[var(--text)] border border-[var(--card-border)] transition-colors"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <Download className="w-3 h-3" />
                    {t('cdg')}
                  </a>
                )}
                {downloadUrls?.packages?.txt_zip && (
                  <a
                    href={api.getDownloadUrl(job.job_id, "packages", "txt_zip")}
                    className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded bg-[#252525] hover:bg-[#333333] text-[var(--text)] border border-[var(--card-border)] transition-colors"
                    onClick={(e) => e.stopPropagation()}
                  >
                    <Download className="w-3 h-3" />
                    {t('txt')}
                  </a>
                )}
            </>
          )}

          {/* Change Visibility Button */}
          {canChangeVisibility && (
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); setShowVisibilityDialog(true) }}
              disabled={isChangingVisibility}
              className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded bg-[#252525] hover:bg-[#333333] text-[var(--text)] border border-[var(--card-border)] transition-colors disabled:opacity-50"
              title={isPrivate ? "Make this track public" : "Make this track private"}
            >
              {isChangingVisibility ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : isPrivate ? (
                <Globe className="w-3 h-3" />
              ) : (
                <Lock className="w-3 h-3" />
              )}
              {isChangingVisibility
                ? (isPrivate ? t('makingPublic') : t('makingPrivate'))
                : (isPrivate ? t('makePublic') : t('makePrivate'))
              }
            </button>
          )}

          {/* Admin Email Tools (only when job has outputs) */}
          {isAdmin && hasOutputs && (
                <div className="flex">
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); setShowEmailDialog(true) }}
                    className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded-l bg-[#252525] hover:bg-[#333333] text-[var(--text)] border border-[var(--card-border)] border-r-0 transition-colors"
                    title="Send completion email"
                  >
                    <Mail className="w-3 h-3" />
                    {t('email')}
                  </button>
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); copyCompletionMessage() }}
                    disabled={isLoadingMessage}
                    className="inline-flex items-center text-xs px-2 py-1.5 rounded-r bg-[#1a1a1a] hover:bg-[#2a2a2a] text-[var(--text)] border border-[var(--card-border)] disabled:opacity-50 transition-colors"
                    title={t('copyCompletionMessage')}
                  >
                    {isLoadingMessage ? (
                      <Loader2 className="w-3 h-3 animate-spin" />
                    ) : copiedMessage ? (
                      <span className="text-[10px]">{t('copied')}</span>
                    ) : (
                      <Copy className="w-3 h-3" />
                    )}
                  </button>
                </div>
          )}

          {/* Edit Track (visible when job is complete and has outputs) */}
          {job.status === "complete" && !outputsDeleted && hasOutputs && (
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); setShowEditModal(true) }}
                  className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded bg-amber-600 hover:bg-amber-500 text-white transition-colors"
                  title="Edit lyrics, instrumental, or metadata"
                >
                  <Pencil className="w-3 h-3" />
                  Edit
                </button>
          )}

          {/* Admin Link (always visible for admins) */}
          {isAdmin && (
                <a
                  href={`/admin/jobs/?id=${job.job_id}`}
                  onClick={(e) => e.stopPropagation()}
                  className="inline-flex items-center gap-1 text-xs px-3 py-1.5 rounded bg-[#252525] hover:bg-[#333333] text-[var(--text)] border border-[var(--card-border)] transition-colors"
                  title="View job details in admin"
                >
                  <Settings className="w-3 h-3" />
                  {t('admin')}
                </a>
          )}
        </div>
      )}

      {!hasOutputs && !isAdmin && !canChangeVisibility && (
        <p className="text-xs" style={{ color: 'var(--text-muted)' }}>{tCard('noOutputsAvailable')}</p>
      )}

      {/* Send Email Dialog */}
      <Dialog open={showEmailDialog} onOpenChange={setShowEmailDialog}>
        <DialogContent className="sm:max-w-[425px]" onClick={(e) => e.stopPropagation()}>
          <DialogHeader>
            <DialogTitle>{t('sendCompletionEmail')}</DialogTitle>
            <DialogDescription>
              {t('sendToEmailDesc')}
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 py-4">
            <div className="grid grid-cols-4 items-center gap-4">
              <Label htmlFor="email" className="text-right">
                {t('emailAddress')}
              </Label>
              <Input
                id="email"
                type="email"
                value={emailInput}
                onChange={(e) => setEmailInput(e.target.value)}
                placeholder={t('emailPlaceholder')}
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
              {tc('cancel')}
            </Button>
            <Button
              type="button"
              onClick={handleSendEmail}
              disabled={isSendingEmail || !emailInput.trim() || emailSent}
            >
              {isSendingEmail ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  {t('sending')}
                </>
              ) : emailSent ? (
                t('sent')
              ) : (
                t('sendEmail')
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Change Visibility Confirmation Dialog */}
      <AlertDialog open={showVisibilityDialog} onOpenChange={setShowVisibilityDialog}>
        <AlertDialogContent onClick={(e) => e.stopPropagation()}>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {isPrivate ? t('makePublicTitle') : t('makePrivateTitle')}
            </AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-3">
                {isPrivate ? (
                  <>
                    <p>{t('thisWill')}</p>
                    <ul className="list-disc pl-5 space-y-1">
                      <li>{t('removeCustomStyling')}</li>
                      <li>{t('regenerateTitleEndScreens')}</li>
                      <li>{t('reRenderWithDefault')}</li>
                      <li>{t('publishToAll')}</li>
                    </ul>
                    <p>{t('takes15To30Minutes')}</p>
                    <p className="font-medium" style={{ color: 'var(--text)' }}>
                      {t('customStylingRemoved')}
                    </p>
                  </>
                ) : (
                  <>
                    <p>{t('thisWill')}</p>
                    <ul className="list-disc pl-5 space-y-1">
                      <li>{t('removeFromYoutube')}</li>
                      <li>{t('removeFromGoogleDrive')}</li>
                      <li>{t('moveDropboxPrivate')}</li>
                      <li>{t('assignPrivateBrand')}</li>
                    </ul>
                    <p>{t('videoContentWontChange')}</p>
                    <p className="text-sm" style={{ color: 'var(--text-muted)' }}>
                      {t('canChangeBack')}
                    </p>
                  </>
                )}
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{tc('cancel')}</AlertDialogCancel>
            <AlertDialogAction onClick={handleChangeVisibility}>
              {isPrivate ? t('makePublic') : t('makePrivate')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Edit Track Modal */}
      <EditTrackModal
        job={job}
        open={showEditModal}
        onOpenChange={setShowEditModal}
        onEditStarted={onJobUpdated}
      />
    </div>
  )
}
