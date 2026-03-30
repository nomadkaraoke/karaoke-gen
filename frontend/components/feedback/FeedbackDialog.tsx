'use client'

import { useState } from 'react'
import { Star, Loader2, CheckCircle, Gift } from 'lucide-react'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { Checkbox } from '@/components/ui/checkbox'
import { api } from '@/lib/api'
import { useAuth } from '@/lib/auth'

interface FeedbackDialogProps {
  open: boolean
  onClose: () => void
}

function StarRating({ value, onChange, label }: {
  value: number
  onChange: (v: number) => void
  label: string
}) {
  return (
    <div className="space-y-1">
      <Label className="text-sm">{label}</Label>
      <div className="flex gap-1">
        {[1, 2, 3, 4, 5].map((star) => (
          <button
            key={star}
            type="button"
            onClick={() => onChange(star)}
            className="p-0.5 transition-colors"
          >
            <Star
              className={`w-5 h-5 ${
                star <= value
                  ? 'fill-yellow-400 text-yellow-400'
                  : 'text-muted-foreground'
              }`}
            />
          </button>
        ))}
      </div>
    </div>
  )
}

export function FeedbackDialog({ open, onClose }: FeedbackDialogProps) {
  const { fetchUser } = useAuth()
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [isSuccess, setIsSuccess] = useState(false)
  const [error, setError] = useState('')
  const [creditsGranted, setCreditsGranted] = useState(0)

  // Ratings
  const [overallRating, setOverallRating] = useState(0)
  const [easeOfUseRating, setEaseOfUseRating] = useState(0)
  const [lyricsAccuracyRating, setLyricsAccuracyRating] = useState(0)
  const [correctionRating, setCorrectionRating] = useState(0)

  // Text fields
  const [whatWentWell, setWhatWentWell] = useState('')
  const [whatCouldImprove, setWhatCouldImprove] = useState('')
  const [additionalComments, setAdditionalComments] = useState('')

  // Checkboxes
  const [wouldRecommend, setWouldRecommend] = useState(true)
  const [wouldUseAgain, setWouldUseAgain] = useState(true)

  const hasDetailedFeedback =
    whatWentWell.length > 50 ||
    whatCouldImprove.length > 50 ||
    additionalComments.length > 50

  const allRatingsSet =
    overallRating > 0 &&
    easeOfUseRating > 0 &&
    lyricsAccuracyRating > 0 &&
    correctionRating > 0

  const canSubmit = allRatingsSet && hasDetailedFeedback && !isSubmitting

  const longestText = Math.max(
    whatWentWell.length,
    whatCouldImprove.length,
    additionalComments.length
  )

  const handleSubmit = async () => {
    setError('')
    setIsSubmitting(true)
    try {
      const result = await api.submitFeedback({
        overall_rating: overallRating,
        ease_of_use_rating: easeOfUseRating,
        lyrics_accuracy_rating: lyricsAccuracyRating,
        correction_experience_rating: correctionRating,
        what_went_well: whatWentWell || undefined,
        what_could_improve: whatCouldImprove || undefined,
        additional_comments: additionalComments || undefined,
        would_recommend: wouldRecommend,
        would_use_again: wouldUseAgain,
      })
      setCreditsGranted(result.credits_granted)
      setIsSuccess(true)
      // Refresh user data to update credits and feedback_eligible
      fetchUser()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to submit feedback')
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleClose = () => {
    if (isSuccess) {
      // Reset form state for next time
      setOverallRating(0)
      setEaseOfUseRating(0)
      setLyricsAccuracyRating(0)
      setCorrectionRating(0)
      setWhatWentWell('')
      setWhatCouldImprove('')
      setAdditionalComments('')
      setWouldRecommend(true)
      setWouldUseAgain(true)
      setIsSuccess(false)
      setCreditsGranted(0)
    }
    setError('')
    onClose()
  }

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && handleClose()}>
      <DialogContent className="sm:max-w-lg bg-card border-border max-h-[90vh] overflow-y-auto">
        {isSuccess ? (
          <div className="text-center py-6 space-y-4">
            <CheckCircle className="w-16 h-16 text-green-500 mx-auto" />
            <DialogHeader>
              <DialogTitle className="text-foreground text-xl">Thank You!</DialogTitle>
              <DialogDescription className="text-muted-foreground text-base">
                Your feedback helps us improve Nomad Karaoke for everyone.
              </DialogDescription>
            </DialogHeader>
            <div className="bg-green-500/10 border border-green-500/30 rounded-lg p-4 mx-auto max-w-xs">
              <p className="text-2xl font-bold text-green-500">+{creditsGranted}</p>
              <p className="text-sm text-muted-foreground">credits added to your account</p>
            </div>
            <Button onClick={handleClose} className="mt-4">
              Done
            </Button>
          </div>
        ) : (
          <>
            <DialogHeader>
              <DialogTitle className="text-foreground flex items-center gap-2">
                <Gift className="w-5 h-5 text-green-500" />
                Share Your Feedback — Earn 1 Free Credit
              </DialogTitle>
              <DialogDescription className="text-muted-foreground">
                Tell us about your experience creating karaoke videos. Your feedback directly shapes what we build next.
              </DialogDescription>
            </DialogHeader>

            <div className="space-y-5 mt-2">
              {/* Star Ratings */}
              <div className="grid grid-cols-2 gap-4">
                <StarRating
                  label="Overall Experience"
                  value={overallRating}
                  onChange={setOverallRating}
                />
                <StarRating
                  label="Ease of Use"
                  value={easeOfUseRating}
                  onChange={setEaseOfUseRating}
                />
                <StarRating
                  label="Lyrics Accuracy"
                  value={lyricsAccuracyRating}
                  onChange={setLyricsAccuracyRating}
                />
                <StarRating
                  label="Correction Experience"
                  value={correctionRating}
                  onChange={setCorrectionRating}
                />
              </div>

              {/* Text Fields */}
              <div className="space-y-3">
                <div className="space-y-1.5">
                  <Label htmlFor="went-well">What went well?</Label>
                  <Textarea
                    id="went-well"
                    value={whatWentWell}
                    onChange={(e) => setWhatWentWell(e.target.value)}
                    placeholder="What did you enjoy about creating karaoke videos?"
                    rows={2}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="could-improve">What could be better?</Label>
                  <Textarea
                    id="could-improve"
                    value={whatCouldImprove}
                    onChange={(e) => setWhatCouldImprove(e.target.value)}
                    placeholder="What would make the experience better?"
                    rows={2}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="additional">Additional comments</Label>
                  <Textarea
                    id="additional"
                    value={additionalComments}
                    onChange={(e) => setAdditionalComments(e.target.value)}
                    placeholder="Anything else you'd like us to know?"
                    rows={2}
                  />
                </div>
                {!hasDetailedFeedback && (
                  <p className="text-xs text-muted-foreground">
                    Please write at least 50 characters in one of the fields above ({longestText}/50)
                  </p>
                )}
              </div>

              {/* Checkboxes */}
              <div className="space-y-3">
                <div className="flex items-center gap-2">
                  <Checkbox
                    id="would-recommend"
                    checked={wouldRecommend}
                    onCheckedChange={(checked) => setWouldRecommend(checked === true)}
                  />
                  <Label htmlFor="would-recommend" className="text-sm font-normal cursor-pointer">
                    I would recommend Nomad Karaoke to others
                  </Label>
                </div>
                <div className="flex items-center gap-2">
                  <Checkbox
                    id="would-use-again"
                    checked={wouldUseAgain}
                    onCheckedChange={(checked) => setWouldUseAgain(checked === true)}
                  />
                  <Label htmlFor="would-use-again" className="text-sm font-normal cursor-pointer">
                    I would use Nomad Karaoke again
                  </Label>
                </div>
              </div>

              {/* Error */}
              {error && (
                <p className="text-sm text-destructive">{error}</p>
              )}

              {/* Submit */}
              <Button
                onClick={handleSubmit}
                disabled={!canSubmit}
                className="w-full"
              >
                {isSubmitting ? (
                  <>
                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                    Submitting...
                  </>
                ) : (
                  'Submit Feedback & Earn 1 Credit'
                )}
              </Button>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
