'use client'

import { useState, useEffect } from 'react'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { Slider } from '@/components/ui/slider'
import { Badge } from '@/components/ui/badge'
import { Alert, AlertDescription } from '@/components/ui/alert'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { CorrectionAnnotation, CorrectionAnnotationType, CorrectionAction } from '@/lib/lyrics-review/types'

interface CorrectionAnnotationModalProps {
  open: boolean
  onClose: () => void
  onSave: (annotation: Omit<CorrectionAnnotation, 'annotation_id' | 'timestamp'>) => void
  onSkip: () => void
  originalText: string
  correctedText: string
  wordIdsAffected: string[]
  agenticProposal?: {
    action: string
    replacement_text?: string
    confidence: number
    reason: string
    gap_category?: string
  }
  referenceSources: string[]
  audioHash: string
  artist: string
  title: string
  sessionId: string
  gapId?: string
}

const ANNOTATION_TYPES: { value: CorrectionAnnotationType; label: string; description: string }[] = [
  {
    value: 'SOUND_ALIKE',
    label: 'Sound-Alike Error',
    description: 'Homophones or similar-sounding words (e.g., "out" vs "now")',
  },
  {
    value: 'BACKGROUND_VOCALS',
    label: 'Background Vocals',
    description: 'Backing vocals that should be removed from karaoke',
  },
  {
    value: 'EXTRA_WORDS',
    label: 'Extra Filler Words',
    description: 'Transcription added words like "And", "But", "Well"',
  },
  {
    value: 'PUNCTUATION_ONLY',
    label: 'Punctuation/Style Only',
    description: 'Only punctuation or capitalization differences',
  },
  {
    value: 'REPEATED_SECTION',
    label: 'Repeated Section',
    description: 'Chorus or verse repetition not in condensed references',
  },
  {
    value: 'COMPLEX_MULTI_ERROR',
    label: 'Complex Multi-Error',
    description: 'Multiple different error types in one section',
  },
  {
    value: 'AMBIGUOUS',
    label: 'Ambiguous',
    description: 'Unclear without listening to audio',
  },
  {
    value: 'NO_ERROR',
    label: 'No Error',
    description: 'Transcription matches at least one reference source',
  },
  {
    value: 'MANUAL_EDIT',
    label: 'Manual Edit',
    description: 'Human-initiated correction not from detected gap',
  },
]

const CONFIDENCE_LABELS: Record<number, string> = {
  1: '1 - Very Uncertain',
  2: '2 - Somewhat Uncertain',
  3: '3 - Neutral',
  4: '4 - Fairly Confident',
  5: '5 - Very Confident',
}

export default function CorrectionAnnotationModal({
  open,
  onClose,
  onSave,
  onSkip,
  originalText,
  correctedText,
  wordIdsAffected,
  agenticProposal,
  referenceSources,
  audioHash,
  artist,
  title,
  sessionId,
  gapId,
}: CorrectionAnnotationModalProps) {
  const [annotationType, setAnnotationType] = useState<CorrectionAnnotationType>('MANUAL_EDIT')
  const [actionTaken, setActionTaken] = useState<CorrectionAction>('REPLACE')
  const [confidence, setConfidence] = useState(3)
  const [reasoning, setReasoning] = useState('')
  const [agenticAgreed, setAgenticAgreed] = useState(false)
  const [error, setError] = useState('')

  // Pre-fill if we have an agentic proposal
  useEffect(() => {
    if (agenticProposal && agenticProposal.gap_category) {
      setAnnotationType(agenticProposal.gap_category as CorrectionAnnotationType)
      const agreed = agenticProposal.replacement_text
        ? correctedText.toLowerCase().includes(agenticProposal.replacement_text.toLowerCase())
        : false
      setAgenticAgreed(agreed)
    }
  }, [agenticProposal, correctedText])

  // Determine action type based on correction
  useEffect(() => {
    if (originalText === correctedText) {
      setActionTaken('NO_ACTION')
    } else if (correctedText === '') {
      setActionTaken('DELETE')
    } else if (originalText === '') {
      setActionTaken('INSERT')
    } else {
      setActionTaken('REPLACE')
    }
  }, [originalText, correctedText])

  const handleSave = () => {
    if (reasoning.length < 10) {
      setError('Reasoning must be at least 10 characters')
      return
    }

    const annotation: Omit<CorrectionAnnotation, 'annotation_id' | 'timestamp'> = {
      audio_hash: audioHash,
      gap_id: gapId || null,
      annotation_type: annotationType,
      action_taken: actionTaken,
      original_text: originalText,
      corrected_text: correctedText,
      confidence,
      reasoning,
      word_ids_affected: wordIdsAffected,
      agentic_proposal: agenticProposal || null,
      agentic_category: agenticProposal?.gap_category || null,
      agentic_agreed: agenticAgreed,
      reference_sources_consulted: referenceSources,
      artist,
      title,
      session_id: sessionId,
    }

    onSave(annotation)
    handleClose()
  }

  const handleClose = () => {
    setAnnotationType('MANUAL_EDIT')
    setConfidence(3)
    setReasoning('')
    setError('')
    setAgenticAgreed(false)
    onClose()
  }

  const handleSkipAndClose = () => {
    handleClose()
    onSkip()
  }

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && handleClose()}>
      <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Annotate Your Correction</DialogTitle>
          <p className="text-sm text-muted-foreground">
            Help improve the AI by explaining your correction
          </p>
        </DialogHeader>

        <div className="space-y-6">
          {/* Show what changed */}
          <div className="bg-muted p-4 rounded-lg">
            <h4 className="text-sm font-medium mb-2">Your Correction:</h4>
            <div className="flex items-center gap-4">
              <div className="flex-1">
                <span className="text-xs text-destructive">Original:</span>
                <p className="font-mono text-sm line-through text-destructive">
                  {originalText || '(empty)'}
                </p>
              </div>
              <span className="text-lg">→</span>
              <div className="flex-1">
                <span className="text-xs text-green-500">Corrected:</span>
                <p className="font-mono text-sm font-bold text-green-500">
                  {correctedText || '(empty)'}
                </p>
              </div>
            </div>
          </div>

          {/* Show AI suggestion if available */}
          {agenticProposal && (
            <Alert variant={agenticAgreed ? 'default' : 'default'}>
              <AlertDescription>
                <h4 className="font-medium mb-2">AI Suggestion:</h4>
                <p className="text-sm">
                  Category: <strong>{agenticProposal.gap_category}</strong>
                </p>
                <p className="text-sm">
                  Action: <strong>{agenticProposal.action}</strong>
                  {agenticProposal.replacement_text && ` → "${agenticProposal.replacement_text}"`}
                </p>
                <p className="text-sm">Reason: {agenticProposal.reason}</p>
                <p className="text-sm mt-2">
                  {agenticAgreed
                    ? '✓ Your correction matches the AI suggestion'
                    : '✗ Your correction differs from the AI suggestion'}
                </p>
              </AlertDescription>
            </Alert>
          )}

          {/* Reference sources */}
          {referenceSources.length > 0 && (
            <div>
              <h4 className="text-sm font-medium mb-2">Reference Sources Consulted:</h4>
              <div className="flex flex-wrap gap-2">
                {referenceSources.map((source) => (
                  <Badge key={source} variant="secondary">
                    {source}
                  </Badge>
                ))}
              </div>
            </div>
          )}

          {/* Annotation type */}
          <div className="space-y-2">
            <Label>Correction Type *</Label>
            <Select value={annotationType} onValueChange={(v) => setAnnotationType(v as CorrectionAnnotationType)}>
              <SelectTrigger>
                <SelectValue placeholder="Select correction type" />
              </SelectTrigger>
              <SelectContent>
                {ANNOTATION_TYPES.map((type) => (
                  <SelectItem key={type.value} value={type.value}>
                    <div>
                      <p className="font-medium">{type.label}</p>
                      <p className="text-xs text-muted-foreground">{type.description}</p>
                    </div>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Confidence slider */}
          <div className="space-y-2">
            <Label>Confidence in Your Correction *</Label>
            <Slider
              value={[confidence]}
              min={1}
              max={5}
              step={1}
              onValueChange={([v]) => setConfidence(v)}
            />
            <p className="text-sm text-muted-foreground">{CONFIDENCE_LABELS[confidence]}</p>
          </div>

          {/* Reasoning text area */}
          <div className="space-y-2">
            <Label htmlFor="reasoning">Reasoning *</Label>
            <Textarea
              id="reasoning"
              value={reasoning}
              onChange={(e) => {
                setReasoning(e.target.value)
                setError('')
              }}
              placeholder="Explain why this correction is needed (minimum 10 characters)..."
              rows={4}
              className={error ? 'border-destructive' : ''}
            />
            <p className={`text-sm ${error ? 'text-destructive' : 'text-muted-foreground'}`}>
              {error || `${reasoning.length}/10 minimum characters`}
            </p>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={handleSkipAndClose}>
            Skip
          </Button>
          <Button onClick={handleSave}>Save & Continue</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
