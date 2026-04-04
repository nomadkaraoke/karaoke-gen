"use client"

import { useState } from "react"
import { useTranslations } from 'next-intl'
import { api, Job } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Loader2 } from "lucide-react"
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

interface EditTrackModalProps {
  job: Job
  open: boolean
  onOpenChange: (open: boolean) => void
  onEditStarted?: () => void
}

export function EditTrackModal({ job, open, onOpenChange, onEditStarted }: EditTrackModalProps) {
  const t = useTranslations('editTrack')
  const tc = useTranslations('common')
  const [updateMetadata, setUpdateMetadata] = useState(false)
  const [artist, setArtist] = useState(job.artist || "")
  const [title, setTitle] = useState(job.title || "")
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleConfirm = async () => {
    setIsSubmitting(true)
    setError(null)

    try {
      const updates: { artist?: string; title?: string } = {}
      if (updateMetadata) {
        const trimmedArtist = artist?.trim()
        const trimmedTitle = title?.trim()
        if (trimmedArtist && trimmedArtist !== job.artist) updates.artist = trimmedArtist
        if (trimmedTitle && trimmedTitle !== job.title) updates.title = trimmedTitle
      }

      const result = await api.editCompletedTrack(job.job_id, updates)

      onOpenChange(false)

      // Navigate to review screen using server-provided URL
      if (result.review_url) {
        window.location.hash = result.review_url.replace(/^\/app\/jobs#/, '')
      } else {
        window.location.hash = `/${job.job_id}/review`
      }

      onEditStarted?.()
    } catch (err: any) {
      setError(err.message || "Failed to start edit. Please try again.")
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleOpenChange = (newOpen: boolean) => {
    if (!isSubmitting) {
      onOpenChange(newOpen)
      if (!newOpen) {
        setError(null)
        setUpdateMetadata(false)
        setArtist(job.artist || "")
        setTitle(job.title || "")
      }
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-[480px]" onClick={(e) => e.stopPropagation()}>
        <DialogHeader>
          <DialogTitle>{t('title')}</DialogTitle>
          <DialogDescription>
            {t('description')}
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 py-4">
          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              id="update-metadata"
              checked={updateMetadata}
              onChange={(e) => setUpdateMetadata(e.target.checked)}
              className="rounded"
            />
            <Label htmlFor="update-metadata" className="cursor-pointer">
              {t('updateArtistTitle')}
            </Label>
          </div>

          {updateMetadata && (
            <div className="grid gap-3 pl-6">
              <div className="grid grid-cols-4 items-center gap-4">
                <Label htmlFor="edit-artist" className="text-right">
                  {t('artist')}
                </Label>
                <Input
                  id="edit-artist"
                  value={artist}
                  onChange={(e) => setArtist(e.target.value)}
                  className="col-span-3"
                />
              </div>
              <div className="grid grid-cols-4 items-center gap-4">
                <Label htmlFor="edit-title" className="text-right">
                  {t('songTitle')}
                </Label>
                <Input
                  id="edit-title"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  className="col-span-3"
                />
              </div>
            </div>
          )}

          {error && (
            <p className="text-sm text-red-500">{error}</p>
          )}
        </div>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => handleOpenChange(false)}
            disabled={isSubmitting}
          >
            {tc('cancel')}
          </Button>
          <Button
            type="button"
            onClick={handleConfirm}
            disabled={isSubmitting}
          >
            {isSubmitting ? (
              <>
                <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                {t('cleaningUp')}
              </>
            ) : (
              t('confirmEdit')
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
