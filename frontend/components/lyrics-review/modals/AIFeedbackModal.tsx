'use client'

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
          <DialogTitle>AI Suggestion</DialogTitle>
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
            <Label htmlFor="action">Action</Label>
            <Select value={reviewerAction} onValueChange={setAction}>
              <SelectTrigger id="action">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="ACCEPT">Accept</SelectItem>
                <SelectItem value="REJECT">Reject</SelectItem>
                <SelectItem value="MODIFY">Modify</SelectItem>
              </SelectContent>
            </Select>
          </div>

          {reviewerAction === 'MODIFY' && (
            <div className="space-y-2">
              <Label htmlFor="finalText">Final Text</Label>
              <Input
                id="finalText"
                value={finalText}
                onChange={(e) => setFinalText(e.target.value)}
                placeholder="Enter the modified text..."
              />
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="reason">Reason</Label>
            <Select value={reasonCategory} onValueChange={setReason}>
              <SelectTrigger id="reason">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="AI_CORRECT">AI_CORRECT</SelectItem>
                <SelectItem value="AI_INCORRECT">AI_INCORRECT</SelectItem>
                <SelectItem value="AI_SUBOPTIMAL">AI_SUBOPTIMAL</SelectItem>
                <SelectItem value="CONTEXT_NEEDED">CONTEXT_NEEDED</SelectItem>
                <SelectItem value="SUBJECTIVE_PREFERENCE">SUBJECTIVE_PREFERENCE</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label htmlFor="details">Details</Label>
            <Textarea
              id="details"
              value={reasonDetail}
              onChange={(e) => setDetail(e.target.value)}
              placeholder="Additional details..."
              rows={3}
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={handleSubmit}>Submit</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
