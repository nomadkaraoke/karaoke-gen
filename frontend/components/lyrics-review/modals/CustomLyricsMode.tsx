'use client'

import { useTranslations } from 'next-intl'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, X, Sparkles, FileText, Type, AlertTriangle, Check, Upload, Info } from 'lucide-react'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { LyricsSegment } from '@/lib/lyrics-review/types'
import { segmentsFromLines } from '@/lib/lyrics-review/utils/segmentsFromLines'
import { generateCustomLyrics, CustomLyricsApiError } from '@/lib/api/customLyrics'

const ACCEPTED_MIME_TYPES = [
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/pdf',
  'text/plain',
  'text/markdown',
]

const ACCEPTED_EXTENSIONS = ['.docx', '.pdf', '.txt', '.md']
const MAX_FILE_BYTES = 5 * 1024 * 1024

type Phase = 'input' | 'generating' | 'preview'
type InputTab = 'text' | 'file'

interface CustomLyricsModeProps {
  open: boolean
  jobId: string
  artist?: string
  title?: string
  authToken?: string
  existingSegments: LyricsSegment[]
  onSave: (newSegments: LyricsSegment[], meta: { source: 'text' | 'file'; filename?: string; model: string }) => void
  onCancel: () => void
  onBack: () => void
}

export default function CustomLyricsMode({
  open,
  jobId,
  artist,
  title,
  authToken,
  existingSegments,
  onSave,
  onCancel,
  onBack,
}: CustomLyricsModeProps) {
  const t = useTranslations('lyricsReview.modals.customLyricsMode')

  const [phase, setPhase] = useState<Phase>('input')
  const [inputTab, setInputTab] = useState<InputTab>('text')
  const [customText, setCustomText] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [fileError, setFileError] = useState<string | null>(null)
  const [notes, setNotes] = useState('')
  const [generatedText, setGeneratedText] = useState('')
  const [warnings, setWarnings] = useState<string[]>([])
  const [lineCountMismatch, setLineCountMismatch] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [modelUsed, setModelUsed] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  // Reset state on open
  useEffect(() => {
    if (open) {
      setPhase('input')
      setInputTab('text')
      setCustomText('')
      setFile(null)
      setFileError(null)
      setNotes('')
      setGeneratedText('')
      setWarnings([])
      setLineCountMismatch(false)
      setError(null)
      setModelUsed(null)
    }
  }, [open])

  // Abort any in-flight request on unmount
  useEffect(() => {
    return () => {
      abortRef.current?.abort()
    }
  }, [])

  const existingLines = useMemo(
    () => existingSegments.map((s) => s.text.trim()),
    [existingSegments],
  )

  const canGenerate = useMemo(() => {
    if (inputTab === 'text') return customText.trim().length > 0
    return file !== null
  }, [inputTab, customText, file])

  const handleFileSelect = useCallback((selected: File | null) => {
    setFileError(null)
    if (!selected) {
      setFile(null)
      return
    }
    const ext = '.' + (selected.name.split('.').pop() ?? '').toLowerCase()
    if (
      !ACCEPTED_EXTENSIONS.includes(ext) &&
      !ACCEPTED_MIME_TYPES.includes(selected.type)
    ) {
      setFileError(t('fileTypeError', { allowed: ACCEPTED_EXTENSIONS.join(', ') }))
      setFile(null)
      return
    }
    if (selected.size > MAX_FILE_BYTES) {
      setFileError(t('fileSizeError', { maxMb: 5 }))
      setFile(null)
      return
    }
    setFile(selected)
  }, [t])

  const runGenerate = useCallback(async () => {
    setError(null)
    setWarnings([])
    setLineCountMismatch(false)
    setPhase('generating')

    abortRef.current?.abort()
    const ac = new AbortController()
    abortRef.current = ac

    try {
      const response = await generateCustomLyrics(
        jobId,
        {
          existingLines,
          customText: inputTab === 'text' ? customText : undefined,
          file: inputTab === 'file' && file ? file : undefined,
          notes: notes.trim() || undefined,
          artist,
          title,
        },
        ac.signal,
        authToken,
      )

      setGeneratedText(response.lines.join('\n'))
      setWarnings(response.warnings)
      setLineCountMismatch(response.lineCountMismatch)
      setModelUsed(response.model)
      setPhase('preview')
    } catch (err) {
      if ((err as Error).name === 'AbortError') return
      const message =
        err instanceof CustomLyricsApiError
          ? err.message
          : (err as Error).message || t('genericError')
      setError(message)
      setPhase('input')
    }
  }, [
    jobId,
    existingLines,
    inputTab,
    customText,
    file,
    notes,
    artist,
    title,
    authToken,
    t,
  ])

  const previewLineCount = useMemo(
    () => generatedText.split('\n').length,
    [generatedText],
  )
  const expectedLineCount = existingLines.length
  const previewLineDiff = previewLineCount - expectedLineCount
  const canSave = previewLineDiff === 0 && phase === 'preview'

  const handleSave = useCallback(() => {
    const lines = generatedText.split('\n')
    const newSegments = segmentsFromLines(lines, existingSegments)
    onSave(newSegments, {
      source: inputTab,
      filename: file?.name,
      model: modelUsed ?? 'unknown',
    })
  }, [generatedText, existingSegments, onSave, inputTab, file, modelUsed])

  const handleClose = useCallback(() => {
    abortRef.current?.abort()
    onCancel()
  }, [onCancel])

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && handleClose()}>
      <DialogContent className="max-w-2xl h-[80vh] max-h-[80vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Button variant="ghost" size="icon" onClick={onBack} className="h-8 w-8" disabled={phase === 'generating'}>
              <ArrowLeft className="h-4 w-4" />
            </Button>
            <span className="flex-1 flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-primary" />
              {t('title')}
            </span>
            <Button variant="ghost" size="icon" onClick={handleClose} className="h-8 w-8" disabled={phase === 'generating'}>
              <X className="h-4 w-4" />
            </Button>
          </DialogTitle>
        </DialogHeader>

        <div className="flex-1 overflow-hidden flex flex-col gap-4">
          {/* Input phase */}
          {phase === 'input' && (
            <>
              <div className="flex items-start gap-2 p-3 rounded-md bg-muted/50 text-sm text-muted-foreground">
                <Info className="h-4 w-4 mt-0.5 shrink-0" />
                <p>{t('description', { count: expectedLineCount })}</p>
              </div>

              <Tabs value={inputTab} onValueChange={(v) => setInputTab(v as InputTab)}>
                <TabsList className="grid grid-cols-2">
                  <TabsTrigger value="text">
                    <Type className="h-4 w-4 mr-2" />
                    {t('tabText')}
                  </TabsTrigger>
                  <TabsTrigger value="file">
                    <FileText className="h-4 w-4 mr-2" />
                    {t('tabFile')}
                  </TabsTrigger>
                </TabsList>

                <TabsContent value="text" className="mt-3">
                  <Label htmlFor="custom-lyrics-text" className="text-sm">{t('textLabel')}</Label>
                  <Textarea
                    id="custom-lyrics-text"
                    value={customText}
                    onChange={(e) => setCustomText(e.target.value)}
                    placeholder={t('textPlaceholder')}
                    className="mt-1.5 font-mono text-sm min-h-[180px]"
                  />
                </TabsContent>

                <TabsContent value="file" className="mt-3">
                  <Label className="text-sm">{t('fileLabel')}</Label>
                  <div className="mt-1.5 flex items-center gap-3">
                    <label className="inline-flex items-center gap-2 px-3 py-2 rounded-md border border-dashed cursor-pointer hover:bg-muted/30">
                      <Upload className="h-4 w-4" />
                      <span className="text-sm">{t('chooseFile')}</span>
                      <input
                        type="file"
                        accept={ACCEPTED_EXTENSIONS.join(',')}
                        className="sr-only"
                        onChange={(e) => handleFileSelect(e.target.files?.[0] ?? null)}
                      />
                    </label>
                    {file && (
                      <span className="text-sm text-muted-foreground">
                        {file.name} ({(file.size / 1024).toFixed(1)} KB)
                      </span>
                    )}
                  </div>
                  {fileError && (
                    <p className="mt-1.5 text-sm text-destructive flex items-center gap-1">
                      <AlertTriangle className="h-4 w-4" />
                      {fileError}
                    </p>
                  )}
                  <p className="mt-1.5 text-xs text-muted-foreground">
                    {t('fileHint', { allowed: ACCEPTED_EXTENSIONS.join(', '), maxMb: 5 })}
                  </p>
                </TabsContent>
              </Tabs>

              <div>
                <Label htmlFor="custom-lyrics-notes" className="text-sm">{t('notesLabel')}</Label>
                <Textarea
                  id="custom-lyrics-notes"
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder={t('notesPlaceholder')}
                  className="mt-1.5 text-sm min-h-[80px]"
                />
              </div>

              {error && (
                <div className="flex items-start gap-2 p-3 rounded-md bg-destructive/10 text-sm text-destructive">
                  <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
                  <p>{error}</p>
                </div>
              )}
            </>
          )}

          {/* Generating phase */}
          {phase === 'generating' && (
            <div className="flex-1 flex flex-col items-center justify-center gap-4 text-center">
              <Sparkles className="h-10 w-10 text-primary animate-pulse" />
              <div>
                <p className="text-lg font-semibold">{t('generatingTitle')}</p>
                <p className="text-sm text-muted-foreground mt-1">{t('generatingSubtitle')}</p>
              </div>
              <Button variant="outline" size="sm" onClick={() => abortRef.current?.abort()}>
                {t('cancelGeneration')}
              </Button>
            </div>
          )}

          {/* Preview phase — Task 12 */}
          {phase === 'preview' && (
            <>
              <div className="flex items-center justify-between">
                <span
                  className={
                    'text-sm font-medium ' +
                    (previewLineDiff === 0 ? 'text-green-500' : 'text-destructive')
                  }
                >
                  {previewLineDiff === 0 ? (
                    <span className="flex items-center gap-1">
                      <Check className="h-4 w-4" />
                      {previewLineCount}/{expectedLineCount} lines
                    </span>
                  ) : (
                    <span className="flex items-center gap-1">
                      <AlertTriangle className="h-4 w-4" />
                      {previewLineCount}/{expectedLineCount} lines
                    </span>
                  )}
                </span>
                {modelUsed && (
                  <span className="text-xs text-muted-foreground">
                    {t('modelLabel')}: {modelUsed}
                  </span>
                )}
              </div>

              {(warnings.length > 0 || lineCountMismatch) && (
                <div className="flex items-start gap-2 p-3 rounded-md bg-yellow-500/10 text-sm text-yellow-700 dark:text-yellow-300">
                  <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
                  <ul className="list-disc list-inside">
                    {warnings.map((w, i) => (
                      <li key={i}>{w}</li>
                    ))}
                    {lineCountMismatch && warnings.length === 0 && (
                      <li>{t('lineCountMismatchFallback')}</li>
                    )}
                  </ul>
                </div>
              )}

              <Textarea
                value={generatedText}
                onChange={(e) => setGeneratedText(e.target.value)}
                placeholder={t('previewPlaceholder')}
                className="flex-1 resize-none font-mono text-sm min-h-[260px]"
              />

              {previewLineDiff !== 0 && (
                <div className="flex items-start gap-2 p-3 rounded-md bg-destructive/10 text-sm text-destructive">
                  <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
                  <p>
                    {previewLineDiff > 0
                      ? t('tooManyLines', { diff: Math.abs(previewLineDiff), expected: expectedLineCount, actual: previewLineCount })
                      : t('tooFewLines', { diff: Math.abs(previewLineDiff), expected: expectedLineCount, actual: previewLineCount })}
                  </p>
                </div>
              )}
            </>
          )}
</div>

        <DialogFooter>
          {phase === 'input' && (
            <>
              <Button variant="outline" onClick={handleClose}>
                {t('cancel')}
              </Button>
              <Button onClick={runGenerate} disabled={!canGenerate}>
                <Sparkles className="h-4 w-4 mr-2" />
                {t('generate')}
              </Button>
            </>
          )}
          {/* Preview footer added in Task 12 */}
          {phase === 'preview' && (
            <>
              <Button variant="outline" onClick={() => setPhase('input')}>
                {t('backToInput')}
              </Button>
              <Button variant="outline" onClick={runGenerate}>
                <Sparkles className="h-4 w-4 mr-2" />
                {t('regenerate')}
              </Button>
              <Button onClick={handleSave} disabled={!canSave}>
                <Check className="h-4 w-4 mr-2" />
                {t('saveCustom')}
              </Button>
            </>
          )}
          {phase === 'generating' && (
            <Button variant="outline" disabled>
              {t('generating')}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
