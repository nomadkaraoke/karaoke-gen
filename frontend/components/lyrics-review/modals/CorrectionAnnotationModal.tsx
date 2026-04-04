'use client'

import { useTranslations } from 'next-intl'
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

const ANNOTATION_TYPE_KEYS: { value: CorrectionAnnotationType; labelKey: string; descKey: string }[] = [
  { value: 'SOUND_ALIKE', labelKey: 'soundAlike', descKey: 'soundAlikeDesc' },
  { value: 'BACKGROUND_VOCALS', labelKey: 'backgroundVocals', descKey: 'backgroundVocalsDesc' },
  { value: 'EXTRA_WORDS', labelKey: 'extraFiller', descKey: 'extraFillerDesc' },
  { value: 'PUNCTUATION_ONLY', labelKey: 'punctuation', descKey: 'punctuationDesc' },
  { value: 'REPEATED_SECTION', labelKey: 'repeatedSection', descKey: 'repeatedSectionDesc' },
  { value: 'COMPLEX_MULTI_ERROR', labelKey: 'complexMultiError', descKey: 'complexMultiErrorDesc' },
  { value: 'AMBIGUOUS', labelKey: 'ambiguous', descKey: 'ambiguousDesc' },
  { value: 'NO_ERROR', labelKey: 'noError', descKey: 'noErrorDesc' },
  { value: 'MANUAL_EDIT', labelKey: 'manualEdit', descKey: 'manualEditDesc' },
]

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
  const t = useTranslations('lyricsReview.modals.correctionAnnotation')
  const tTypes = useTranslations('lyricsReview.modals.correctionAnnotation.types')
  const tConf = useTranslations('lyricsReview.modals.correctionAnnotation.confidenceLevels')
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
          <DialogTitle>{t('title')}</DialogTitle>
          <p className="text-sm text-muted-foreground">
            {t('description')}
          </p>
        </DialogHeader>

        <div className="space-y-6">
          {/* Show what changed */}
          <div className="bg-muted p-4 rounded-lg">
            <h4 className="text-sm font-medium mb-2">{t('yourCorrection')}</h4>
            <div className="flex items-center gap-4">
              <div className="flex-1">
                <span className="text-xs text-destructive">{t('original')}</span>
                <p className="font-mono text-sm line-through text-destructive">
                  {originalText || '(empty)'}
                </p>
              </div>
              <span className="text-lg">→</span>
              <div className="flex-1">
                <span className="text-xs text-green-500">{t('corrected')}</span>
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
                <h4 className="font-medium mb-2">{t('aiSuggestion')}</h4>
                <p className="text-sm">
                  {t('category')} <strong>{agenticProposal.gap_category}</strong>
                </p>
                <p className="text-sm">
                  {t('action')} <strong>{agenticProposal.action}</strong>
                  {agenticProposal.replacement_text && ` → "${agenticProposal.replacement_text}"`}
                </p>
                <p className="text-sm">{t('reason')} {agenticProposal.reason}</p>
                <p className="text-sm mt-2">
                  {agenticAgreed
                    ? `✓ ${t('matchesAI')}`
                    : `✗ ${t('differsFromAI')}`}
                </p>
              </AlertDescription>
            </Alert>
          )}

          {/* Reference sources */}
          {referenceSources.length > 0 && (
            <div>
              <h4 className="text-sm font-medium mb-2">{t('referenceSources')}</h4>
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
            <Label>{t('correctionType')}</Label>
            <Select value={annotationType} onValueChange={(v) => setAnnotationType(v as CorrectionAnnotationType)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ANNOTATION_TYPE_KEYS.map((type) => (
                  <SelectItem key={type.value} value={type.value}>
                    <div>
                      <p className="font-medium">{tTypes(type.labelKey)}</p>
                      <p className="text-xs text-muted-foreground">{tTypes(type.descKey)}</p>
                    </div>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* Confidence slider */}
          <div className="space-y-2">
            <Label>{t('confidence')}</Label>
            <Slider
              value={[confidence]}
              min={1}
              max={5}
              step={1}
              onValueChange={([v]) => setConfidence(v)}
            />
            <p className="text-sm text-muted-foreground">{tConf(String(confidence))}</p>
          </div>

          {/* Reasoning text area */}
          <div className="space-y-2">
            <Label htmlFor="reasoning">{t('reasoning')}</Label>
            <Textarea
              id="reasoning"
              value={reasoning}
              onChange={(e) => {
                setReasoning(e.target.value)
                setError('')
              }}
              placeholder={t('reasoningPlaceholder')}
              rows={4}
              className={error ? 'border-destructive' : ''}
            />
            <p className={`text-sm ${error ? 'text-destructive' : 'text-muted-foreground'}`}>
              {error || t('minChars', { count: reasoning.length })}
            </p>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={handleSkipAndClose}>
            {t('skip')}
          </Button>
          <Button onClick={handleSave}>{t('saveAndContinue')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
