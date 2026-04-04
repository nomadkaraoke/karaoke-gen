'use client'

import { useTranslations } from 'next-intl'
import { useState, useEffect, useRef } from 'react'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

interface AIFeedbackModalProps {
  open: boolean
  onClose: () => void
  onSubmit: (payload: {
    reviewerAction: string
    finalText?: string
    reasonCategory: string
    reasonDetail?: string
  }) => void
  suggestion?: {
    text: string
    reasoning?: string
    confidence?: number
  }
}

export default function AIFeedbackModal({
  open,
  onClose,
  onSubmit,
  suggestion,
}: AIFeedbackModalProps) {
  const t = useTranslations('lyricsReview.modals.aiFeedback')
  const tc = useTranslations('common')
  const [reviewerAction, setAction] = useState('ACCEPT')
  const [finalText, setFinalText] = useState('')
  const [reasonCategory, setReason] = useState('AI_CORRECT')
  const [reasonDetail, setDetail] = useState('')
  const contentRef = useRef<HTMLDivElement>(null)

  // Focus content on open
  useEffect(() => {
    if (open && contentRef.current) {
      contentRef.current.focus()
    }
  }, [open])

  const handleSubmit = () => {
    onSubmit({
      reviewerAction,
      finalText: finalText || undefined,
      reasonCategory,
      reasonDetail: reasonDetail || undefined,
    })
  }

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <DialogContent className="max-w-md" ref={contentRef}>
        <DialogHeader>
          <DialogTitle>{t('title')}</DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          <div>
            <p className="text-sm">
              {suggestion?.text ?? 'No suggestion'}
              {suggestion?.confidence != null
                ? ` (confidence ${Math.round((suggestion.confidence || 0) * 100)}%)`
                : null}
            </p>
            {suggestion?.reasoning && (
              <p className="text-xs text-muted-foreground mt-1">{suggestion.reasoning}</p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="action">{t('action')}</Label>
            <Select value={reviewerAction} onValueChange={setAction}>
              <SelectTrigger id="action">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="ACCEPT">{t('accept')}</SelectItem>
                <SelectItem value="REJECT">{t('reject')}</SelectItem>
                <SelectItem value="MODIFY">{t('modify')}</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {reviewerAction === 'MODIFY' && (
            <div className="space-y-2">
              <Label htmlFor="finalText">{t('finalText')}</Label>
              <Input
                id="finalText"
                value={finalText}
                onChange={(e) => setFinalText(e.target.value)}
                placeholder={t('finalTextPlaceholder')}
              />
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="reason">{t('reason')}</Label>
            <Select value={reasonCategory} onValueChange={setReason}>
              <SelectTrigger id="reason">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="AI_CORRECT">{t('aiCorrect')}</SelectItem>
                <SelectItem value="AI_INCORRECT">{t('aiIncorrect')}</SelectItem>
                <SelectItem value="AI_SUBOPTIMAL">{t('aiSuboptimal')}</SelectItem>
                <SelectItem value="CONTEXT_NEEDED">{t('contextNeeded')}</SelectItem>
                <SelectItem value="SUBJECTIVE_PREFERENCE">{t('subjectivePreference')}</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label htmlFor="details">{t('details')}</Label>
            <Textarea
              id="details"
              value={reasonDetail}
              onChange={(e) => setDetail(e.target.value)}
              placeholder={t('detailsPlaceholder')}
              rows={3}
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            {tc('cancel')}
          </Button>
          <Button onClick={handleSubmit}>{t('submit')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
