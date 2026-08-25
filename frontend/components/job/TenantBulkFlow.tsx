"use client"

import { useState, useRef, useCallback, useMemo, useEffect } from "react"
import { useTranslations } from 'next-intl'
import { api, ApiError, type BulkAnalyzeResponse } from "@/lib/api"
import { uploadResumable, ResumableUploadError } from "@/lib/resumable-upload"
import {
  saveRowSessions, markRowDone, clearBatch, loadPendingBatch, matchRepickedFile,
  type PersistedRow,
} from "@/lib/upload-recovery"
import {
  Upload, Music, Loader2, AlertTriangle, CheckCircle2, X, FolderOpen, Files, History,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"

const AUDIO_EXTENSIONS = [".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".opus", ".aiff", ".aif"]
const MAX_FILES = 100
// How many jobs to create+upload at once. Bounded so we don't hammer GCS or the
// backend when an operator drops a large folder.
const SUBMIT_CONCURRENCY = 3

type RowStatus = "pending" | "creating" | "uploading" | "completing" | "done" | "error"

interface EditableRow {
  id: string
  artist: string
  title: string
  mixedFilename: string
  instrumentalFilename: string
  confidence: string
  warning: string | null
  status: RowStatus
  progress: number
  error?: string
  jobId?: string
  // Upload URLs from a successful create call (resumable session URIs when the
  // backend supports upload_mode=resumable), kept so a retry after a failed
  // upload/complete resumes the SAME job instead of creating a duplicate.
  uploadUrls?: { file_type: string; upload_url: string; content_type: string; resumable?: boolean }[]
  // Live upload telemetry (resumable engine): throughput, ETA and connection state.
  bytesPerSecond?: number | null
  etaSeconds?: number | null
  uploadState?: "uploading" | "waiting-online" | "retrying"
}

type Phase = "select" | "analyzing" | "review" | "done"

function isAudio(name: string): boolean {
  const lower = name.toLowerCase()
  return AUDIO_EXTENSIONS.some(ext => lower.endsWith(ext))
}

// Stable per-file identity. A folder pick can contain multiple files that share
// a basename across subfolders; webkitRelativePath disambiguates them. We thread
// this identity through analysis, row selection, and upload lookup so a row never
// resolves to the wrong file. The backend parses the basename regardless
// (Path().stem uses the final path component), so pairing is unaffected.
function identityOf(f: File): string {
  return (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name
}

// Row is submittable only when both a mixed AND an instrumental file are chosen
// (tenant jobs require a supplied instrumental — mixed-only rows are blocked and
// flagged), the two differ, artist/title are filled, and both files are present.
function rowIsValid(row: EditableRow, fileMap: Map<string, File>): boolean {
  return Boolean(
    row.artist.trim() &&
    row.title.trim() &&
    row.mixedFilename &&
    row.instrumentalFilename &&
    row.mixedFilename !== row.instrumentalFilename &&
    fileMap.has(row.mixedFilename) &&
    fileMap.has(row.instrumentalFilename)
  )
}

interface TenantBulkFlowProps {
  onJobsChanged: () => void
}

export function TenantBulkFlow({ onJobsChanged }: TenantBulkFlowProps) {
  const t = useTranslations('tenantBulk')

  const [phase, setPhase] = useState<Phase>("select")
  const [files, setFiles] = useState<File[]>([])
  const [rows, setRows] = useState<EditableRow[]>([])
  const [unpaired, setUnpaired] = useState<BulkAnalyzeResponse["unpaired"]>([])
  const [ignored, setIgnored] = useState<BulkAnalyzeResponse["ignored"]>([])
  const [error, setError] = useState("")
  const [isSubmitting, setIsSubmitting] = useState(false)
  // One batch id per review-table lifetime (fresh analyze or recovery), shared
  // by every row's job and by the IndexedDB recovery records.
  const [batchId, setBatchId] = useState<string | null>(null)
  // An unfinished batch found in IndexedDB from a previous session, offered for
  // "re-pick to resume". recoveryMode=true routes the next folder pick to the
  // resume path instead of a fresh analyze.
  const [recovery, setRecovery] = useState<{ batchId: string; rows: PersistedRow[] } | null>(null)
  const recoveryModeRef = useRef(false)

  const folderInputRef = useRef<HTMLInputElement>(null)
  const filesInputRef = useRef<HTMLInputElement>(null)

  // Detect an unfinished batch from an earlier session (page reload / crash).
  useEffect(() => {
    let cancelled = false
    loadPendingBatch().then(pending => {
      if (!cancelled && pending) setRecovery(pending)
    })
    return () => { cancelled = true }
  }, [])

  // Uploads survive most interruptions, but warn before an intentional
  // navigation away mid-batch (the in-memory File handles would be lost;
  // recovery would then require a re-pick).
  useEffect(() => {
    if (!isSubmitting) return
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ""
    }
    window.addEventListener("beforeunload", warn)
    return () => window.removeEventListener("beforeunload", warn)
  }, [isSubmitting])

  // Map file identity -> File for uploads and dropdown validation.
  const fileMap = useMemo(() => {
    const m = new Map<string, File>()
    for (const f of files) m.set(identityOf(f), f)
    return m
  }, [files])

  const audioFilenames = useMemo(
    () => files.filter(f => isAudio(f.name)).map(identityOf).sort(),
    [files]
  )

  const validCount = useMemo(
    () => rows.filter(r => rowIsValid(r, fileMap)).length,
    [rows, fileMap]
  )

  // Rows we can still submit or retry: structurally valid and either not yet
  // started (pending) or previously failed (error). In-flight and done rows are
  // excluded. A retry of an errored row reuses its jobId + stored upload URLs, so
  // it resumes the same job rather than creating a duplicate.
  const submittableCount = useMemo(
    () => rows.filter(r => rowIsValid(r, fileMap) && (r.status === "pending" || r.status === "error")).length,
    [rows, fileMap]
  )

  const resetAll = useCallback(() => {
    setPhase("select")
    setFiles([])
    setRows([])
    setUnpaired([])
    setIgnored([])
    setError("")
    setIsSubmitting(false)
    setBatchId(null)
    recoveryModeRef.current = false
    if (folderInputRef.current) folderInputRef.current.value = ""
    if (filesInputRef.current) filesInputRef.current.value = ""
  }, [])

  const analyze = useCallback(async (selected: File[]) => {
    setError("")
    if (selected.length === 0) return
    // Cap on audio files (candidate tracks), not incidental non-audio files.
    const audioCount = selected.filter(f => isAudio(f.name)).length
    if (audioCount > MAX_FILES) {
      setError(t('tooManyFiles', { max: MAX_FILES }))
      return
    }
    if (audioCount === 0) {
      setError(t('noAudioFound'))
      return
    }
    setFiles(selected)
    setBatchId(null) // fresh analyze → fresh batch
    setPhase("analyzing")
    try {
      const result = await api.analyzeBulk(selected.map(identityOf))
      const editable: EditableRow[] = result.rows.map((r, i) => ({
        id: `row-${i}-${r.mixed_filename}`,
        artist: r.artist,
        title: r.title,
        mixedFilename: r.mixed_filename,
        instrumentalFilename: r.instrumental_filename,
        confidence: r.confidence,
        warning: r.warning,
        status: "pending",
        progress: 0,
      }))
      setRows(editable)
      setUnpaired(result.unpaired)
      setIgnored(result.ignored)
      setPhase("review")
    } catch (err: any) {
      console.error("[TenantBulkFlow] analyze failed:", err)
      setError(err instanceof ApiError ? err.message : t('analyzeError'))
      setPhase("select")
    }
  }, [t])

  // Rebuild the review table from a persisted batch + a re-picked selection.
  // Matched files resume their existing jobs (the resumable engine asks each
  // session for its offset, so nothing already uploaded is re-sent). Unmatched
  // rows appear as invalid so the operator can fix or remove them.
  const resumeFromFiles = useCallback((pending: { batchId: string; rows: PersistedRow[] }, picked: File[]) => {
    setError("")
    setFiles(picked)
    const rebuilt: EditableRow[] = pending.rows.map(pr => {
      const mixed = pr.files.find(f => f.fileType === "audio")
      const instrumental = pr.files.find(f => f.fileType === "existing_instrumental")
      const mixedFile = mixed ? matchRepickedFile(mixed, picked) : null
      const instrumentalFile = instrumental ? matchRepickedFile(instrumental, picked) : null
      return {
        id: pr.rowId,
        artist: pr.artist,
        title: pr.title,
        mixedFilename: mixedFile ? identityOf(mixedFile) : (mixed?.identity ?? ""),
        instrumentalFilename: instrumentalFile ? identityOf(instrumentalFile) : (instrumental?.identity ?? ""),
        confidence: "high",
        warning: null,
        status: "pending",
        progress: 0,
        jobId: pr.jobId,
        uploadUrls: pr.files.map(f => ({
          file_type: f.fileType,
          upload_url: f.sessionUri,
          content_type: "",
          resumable: true,
        })),
      }
    })
    setRows(rebuilt)
    setUnpaired([])
    setIgnored([])
    setBatchId(pending.batchId)
    setRecovery(null)
    recoveryModeRef.current = false
    setPhase("review")
  }, [])

  function handlePick(picked: File[]) {
    if (recoveryModeRef.current && recovery) {
      resumeFromFiles(recovery, picked)
    } else {
      analyze(picked)
    }
  }

  function handleFolderPick(e: React.ChangeEvent<HTMLInputElement>) {
    // Folder pick includes non-audio files; keep them so they surface as "ignored".
    handlePick(Array.from(e.target.files ?? []))
  }

  function handleFilesPick(e: React.ChangeEvent<HTMLInputElement>) {
    handlePick(Array.from(e.target.files ?? []))
  }

  function updateRow(id: string, patch: Partial<EditableRow>) {
    setRows(prev => prev.map(r => (r.id === id ? { ...r, ...patch } : r)))
  }

  function removeRow(id: string) {
    setRows(prev => prev.filter(r => r.id !== id))
  }

  async function submitOneRow(row: EditableRow, currentBatchId: string): Promise<boolean> {
    const mixedFile = fileMap.get(row.mixedFilename)
    const instrumentalFile = fileMap.get(row.instrumentalFilename)
    if (!mixedFile || !instrumentalFile) {
      updateRow(row.id, { status: "error", error: t('rowMissingFile') })
      return false
    }
    // True when this attempt is a retry/resume of a job created earlier.
    const hadExistingJob = Boolean(row.jobId && row.uploadUrls)
    try {
      // Reuse an existing job + its upload URLs when retrying a row that already
      // created a job; otherwise create a fresh job (resumable mode: the backend
      // returns GCS session URIs supporting chunked, mid-file-resumable uploads).
      let jobId = row.jobId
      let uploadUrls = row.uploadUrls
      if (!jobId || !uploadUrls) {
        updateRow(row.id, { status: "creating", progress: 0, error: undefined })
        const createResponse = await api.createJobWithUploadUrls(
          row.artist.trim(),
          row.title.trim(),
          [
            { filename: mixedFile.name, content_type: mixedFile.type || "application/octet-stream", file_type: "audio" },
            { filename: instrumentalFile.name, content_type: instrumentalFile.type || "application/octet-stream", file_type: "existing_instrumental" },
          ],
          { is_private: true, existing_instrumental: true, batch_id: currentBatchId, upload_mode: "resumable" },
        )
        jobId = createResponse.job_id
        uploadUrls = createResponse.upload_urls
        updateRow(row.id, { jobId, uploadUrls })
        // Persist sessions so a page reload / crash can re-pick and resume.
        const persistFile = (file: File, fileType: string, sessionUri: string) => ({
          fileType,
          identity: identityOf(file),
          name: file.name,
          size: file.size,
          lastModified: file.lastModified,
          sessionUri,
        })
        const audioEntry = uploadUrls.find(u => u.file_type === "audio")
        const instEntry = uploadUrls.find(u => u.file_type === "existing_instrumental")
        if (audioEntry?.resumable && instEntry?.resumable) {
          saveRowSessions({
            batchId: currentBatchId,
            rowId: row.id,
            jobId,
            artist: row.artist.trim(),
            title: row.title.trim(),
            createdAt: Date.now(),
            files: [
              persistFile(mixedFile, "audio", audioEntry.upload_url),
              persistFile(instrumentalFile, "existing_instrumental", instEntry.upload_url),
            ],
          })
        }
      }

      updateRow(row.id, { status: "uploading", progress: 0, error: undefined, uploadState: "uploading" })
      const audioUrl = uploadUrls.find(u => u.file_type === "audio")
      const instrumentalUrl = uploadUrls.find(u => u.file_type === "existing_instrumental")
      if (!audioUrl || !instrumentalUrl) throw new ApiError("Missing upload URL", 500)

      // Byte-accurate row progress across both files, with throughput + ETA.
      const totalBytes = mixedFile.size + instrumentalFile.size
      const reportBytes = (loadedBytes: number, rate: number | null, state: EditableRow["uploadState"]) => {
        updateRow(row.id, {
          progress: Math.min(100, Math.round((loadedBytes / totalBytes) * 100)),
          bytesPerSecond: rate,
          etaSeconds: rate && rate > 0 ? Math.max(0, (totalBytes - loadedBytes) / rate) : null,
          uploadState: state,
        })
      }
      const uploadOne = async (
        entry: { upload_url: string; content_type: string; resumable?: boolean },
        file: File,
        baseBytes: number,
      ) => {
        if (entry.resumable) {
          await uploadResumable(entry.upload_url, file, {
            onProgress: p => reportBytes(baseBytes + p.loaded, p.bytesPerSecond, p.state),
          })
        } else {
          // Legacy single-shot signed PUT (backend without resumable support).
          await api.uploadToSignedUrl(entry.upload_url, file, entry.content_type,
            (loaded) => reportBytes(baseBytes + loaded, null, "uploading"))
        }
      }
      await uploadOne(audioUrl, mixedFile, 0)
      await uploadOne(instrumentalUrl, instrumentalFile, mixedFile.size)

      updateRow(row.id, { status: "completing", progress: 100, etaSeconds: 0 })
      try {
        await api.completeJobUpload(jobId, ["audio", "existing_instrumental"])
      } catch (completeErr: any) {
        // A recovered/retried row may have already completed before the previous
        // session died; the job then rejects a second uploads-complete. The
        // uploads themselves are verified done (session offsets), so treat it
        // as success rather than stranding the row.
        const alreadyStarted = hadExistingJob && completeErr instanceof ApiError && completeErr.status === 400
        if (!alreadyStarted) throw completeErr
        console.warn("[TenantBulkFlow] uploads-complete rejected for recovered row; assuming already processing:", jobId)
      }
      markRowDone(currentBatchId, row.id)
      updateRow(row.id, { status: "done" })
      return true
    } catch (err: any) {
      console.error("[TenantBulkFlow] row submit failed:", row.id, err)
      let message = err instanceof ApiError ? err.message : t('rowSubmitError')
      if (err instanceof ResumableUploadError && err.permanent) {
        // Session expired/invalid (e.g. resumed after ~a week) — resuming is
        // impossible; the operator should start this row over.
        message = t('sessionExpired')
      }
      updateRow(row.id, { status: "error", error: message, uploadState: undefined })
      return false
    }
  }

  async function handleSubmitAll() {
    // Submit pending rows and retry previously-failed ones (submitOneRow reuses
    // an existing jobId, so a retry never double-creates for the same track).
    const submittable = rows.filter(r => rowIsValid(r, fileMap) && (r.status === "pending" || r.status === "error"))
    if (submittable.length === 0) return
    setIsSubmitting(true)
    setError("")
    // Reuse the batch id across retries/recovery so jobs and IndexedDB records
    // stay grouped under one batch.
    const currentBatchId = batchId ?? ((typeof crypto !== "undefined" && crypto.randomUUID)
      ? crypto.randomUUID()
      : `batch-${Date.now()}-${Math.round(Math.random() * 1e6)}`)
    setBatchId(currentBatchId)

    // Bounded-concurrency worker pool; track how many rows succeeded.
    const queue = [...submittable]
    let succeeded = 0
    async function worker() {
      while (queue.length > 0) {
        const row = queue.shift()
        if (!row) break
        if (await submitOneRow(row, currentBatchId)) succeeded++
      }
    }
    await Promise.all(Array.from({ length: Math.min(SUBMIT_CONCURRENCY, queue.length) }, worker))

    setIsSubmitting(false)
    onJobsChanged()
    // Show the done summary only if every submittable row succeeded; otherwise
    // stay on the review table so the operator can retry the failed rows.
    if (succeeded === submittable.length) setPhase("done")
  }

  // ---- Select phase -------------------------------------------------------
  if (phase === "select" || phase === "analyzing") {
    return (
      <div className="space-y-4">
        <p className="text-sm" style={{ color: "var(--text-muted)" }}>{t('intro')}</p>

        {/* Unfinished batch from an earlier session → offer re-pick-to-resume. */}
        {recovery && (
          <div className="rounded-lg border p-3 space-y-2" data-testid="resume-banner"
            style={{ borderColor: "var(--tenant-primary, var(--brand-pink))", backgroundColor: "rgba(255,255,255,0.03)" }}>
            <div className="flex items-center gap-2 text-sm font-medium" style={{ color: "var(--text)" }}>
              <History className="w-4 h-4" style={{ color: "var(--tenant-primary, var(--brand-pink))" }} />
              {t('resumeBannerTitle')}
            </div>
            <p className="text-xs" style={{ color: "var(--text-muted)" }}>
              {t('resumeBannerDesc', { count: recovery.rows.length })}
            </p>
            <div className="flex gap-2">
              <Button size="sm"
                onClick={() => { recoveryModeRef.current = true; folderInputRef.current?.click() }}
                style={{ backgroundColor: "var(--tenant-primary, var(--brand-pink))", color: "var(--primary-foreground)" }}>
                {t('resumeChoose')}
              </Button>
              <Button size="sm" variant="outline"
                onClick={() => { clearBatch(recovery.batchId); setRecovery(null) }}
                style={{ borderColor: "var(--card-border)", color: "var(--text-muted)" }}>
                {t('resumeDiscard')}
              </Button>
            </div>
          </div>
        )}

        <div className="grid grid-cols-2 gap-3">
          <button
            type="button"
            disabled={phase === "analyzing"}
            onClick={() => folderInputRef.current?.click()}
            className="flex flex-col items-center gap-2 border-2 border-dashed rounded-lg p-5 text-center transition-colors hover:border-white/30 disabled:opacity-50"
            style={{ borderColor: "var(--card-border)" }}
          >
            <FolderOpen className="w-6 h-6" style={{ color: "var(--tenant-primary, var(--brand-pink))" }} />
            <span className="text-sm font-medium" style={{ color: "var(--text)" }}>{t('pickFolder')}</span>
          </button>
          <button
            type="button"
            disabled={phase === "analyzing"}
            onClick={() => filesInputRef.current?.click()}
            className="flex flex-col items-center gap-2 border-2 border-dashed rounded-lg p-5 text-center transition-colors hover:border-white/30 disabled:opacity-50"
            style={{ borderColor: "var(--card-border)" }}
          >
            <Files className="w-6 h-6" style={{ color: "var(--tenant-primary, var(--brand-pink))" }} />
            <span className="text-sm font-medium" style={{ color: "var(--text)" }}>{t('pickFiles')}</span>
          </button>
        </div>

        {/* @ts-expect-error webkitdirectory is a non-standard but widely-supported attribute */}
        <input ref={folderInputRef} data-testid="bulk-folder-input" type="file" webkitdirectory="" directory="" multiple className="hidden" onChange={handleFolderPick} />
        <input ref={filesInputRef} data-testid="bulk-files-input" type="file" accept={AUDIO_EXTENSIONS.join(",")} multiple className="hidden" onChange={handleFilesPick} />

        {phase === "analyzing" && (
          <div className="flex items-center gap-2 text-sm" style={{ color: "var(--text-muted)" }}>
            <Loader2 className="w-4 h-4 animate-spin" />
            <span>{t('analyzing')}</span>
          </div>
        )}

        {error && (
          <div className="flex items-start gap-2 text-sm p-3 rounded-lg" style={{ backgroundColor: "rgba(239, 68, 68, 0.1)", color: "#ef4444" }}>
            <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}
      </div>
    )
  }

  // ---- Done phase ---------------------------------------------------------
  if (phase === "done") {
    const doneCount = rows.filter(r => r.status === "done").length
    return (
      <div className="space-y-5">
        <div className="flex flex-col items-center text-center py-5 space-y-3">
          <div className="w-12 h-12 rounded-full flex items-center justify-center" style={{ backgroundColor: "rgba(34, 197, 94, 0.15)" }}>
            <CheckCircle2 className="w-7 h-7 text-green-500" />
          </div>
          <div>
            <h2 className="text-lg font-semibold" style={{ color: "var(--text)" }}>{t('allSubmittedTitle')}</h2>
            <p className="text-sm mt-1" style={{ color: "var(--text-muted)" }}>{t('allSubmittedDesc', { count: doneCount })}</p>
          </div>
        </div>
        <Button onClick={resetAll} variant="outline" className="w-full" style={{ borderColor: "var(--card-border)", color: "var(--text)" }}>
          <Music className="w-4 h-4 mr-2" />
          {t('submitAnotherBatch')}
        </Button>
      </div>
    )
  }

  // ---- Review phase -------------------------------------------------------
  const hasWarnings = unpaired.length > 0 || ignored.length > 0
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm" style={{ color: "var(--text-muted)" }}>
          {t('reviewSummary', { rows: rows.length, valid: validCount })}
        </p>
        <button type="button" onClick={resetAll} disabled={isSubmitting}
          className="text-xs underline disabled:opacity-50" style={{ color: "var(--text-muted)" }}>
          {t('startOver')}
        </button>
      </div>

      {/* Warnings strip */}
      {hasWarnings && (
        <div className="rounded-lg p-3 text-xs space-y-1.5" style={{ backgroundColor: "rgba(234, 179, 8, 0.08)", color: "var(--text-muted)" }}>
          <div className="flex items-center gap-1.5 font-medium" style={{ color: "#eab308" }}>
            <AlertTriangle className="w-3.5 h-3.5" />
            {t('warningsTitle')}
          </div>
          {unpaired.map(u => (
            <div key={u.filename} className="truncate">
              • {u.filename} — {u.reason === "no_instrumental" ? t('reasonNoInstrumental') : u.reason === "no_mixed" ? t('reasonNoMixed') : t('reasonUnparseable')}
            </div>
          ))}
          {ignored.length > 0 && (
            <div className="truncate">• {t('ignoredCount', { count: ignored.length })}</div>
          )}
        </div>
      )}

      {/* Review table */}
      <div className="max-h-[420px] overflow-y-auto space-y-2 pr-1">
        {rows.map(row => {
          const valid = rowIsValid(row, fileMap)
          return (
            <div key={row.id} className="rounded-lg border p-3 space-y-2"
              style={{ borderColor: valid ? "var(--card-border)" : "rgba(239,68,68,0.4)", backgroundColor: "rgba(255,255,255,0.02)" }}>
              <div className="grid grid-cols-2 gap-2">
                <Input aria-label={t('artist')} placeholder={t('artist')} value={row.artist}
                  disabled={isSubmitting || row.status === "done"}
                  onChange={e => updateRow(row.id, { artist: e.target.value })}
                  style={{ borderColor: "var(--card-border)", color: "var(--text)", backgroundColor: "var(--input)" }} />
                <Input aria-label={t('title')} placeholder={t('title')} value={row.title}
                  disabled={isSubmitting || row.status === "done"}
                  onChange={e => updateRow(row.id, { title: e.target.value })}
                  style={{ borderColor: "var(--card-border)", color: "var(--text)", backgroundColor: "var(--input)" }} />
              </div>
              <div className="grid grid-cols-2 gap-2">
                <label className="text-xs space-y-1" style={{ color: "var(--text-muted)" }}>
                  <span>{t('mixed')}</span>
                  <select aria-label={t('mixed')} value={row.mixedFilename}
                    disabled={isSubmitting || row.status === "done"}
                    onChange={e => updateRow(row.id, { mixedFilename: e.target.value })}
                    className="w-full h-8 text-xs rounded-md border px-2"
                    style={{ borderColor: "var(--card-border)", color: "var(--text)", backgroundColor: "var(--input)" }}>
                    <option value="">{t('selectFile')}</option>
                    {audioFilenames.map(fn => <option key={fn} value={fn}>{fn}</option>)}
                  </select>
                </label>
                <label className="text-xs space-y-1" style={{ color: "var(--text-muted)" }}>
                  <span>{t('instrumental')}</span>
                  <select aria-label={t('instrumental')} value={row.instrumentalFilename}
                    disabled={isSubmitting || row.status === "done"}
                    onChange={e => updateRow(row.id, { instrumentalFilename: e.target.value })}
                    className="w-full h-8 text-xs rounded-md border px-2"
                    style={{ borderColor: "var(--card-border)", color: "var(--text)", backgroundColor: "var(--input)" }}>
                    <option value="">{t('selectFile')}</option>
                    {audioFilenames.map(fn => <option key={fn} value={fn}>{fn}</option>)}
                  </select>
                </label>
              </div>

              <div className="flex items-center justify-between gap-2">
                <div className="text-xs flex items-center gap-2 min-w-0" style={{ color: "var(--text-muted)" }}>
                  <StatusBadge row={row} valid={valid} t={t} />
                </div>
                {row.status === "pending" && !isSubmitting && (
                  <button type="button" onClick={() => removeRow(row.id)}
                    className="p-1 rounded hover:bg-white/10 shrink-0" style={{ color: "var(--text-muted)" }} aria-label={t('removeRow')}>
                    <X className="w-4 h-4" />
                  </button>
                )}
              </div>

              {/* Surface analyzer uncertainty so an operator doesn't blindly submit
                  a low-confidence or explicitly-warned pairing. */}
              {row.status === "pending" && valid && (row.warning || row.confidence !== "high") && (
                <div className="flex items-start gap-1.5 text-xs" style={{ color: "#eab308" }}>
                  <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                  <span>{row.warning || (row.confidence === "low" ? t('lowConfidence') : t('checkPairing'))}</span>
                </div>
              )}

              {(row.status === "uploading" || row.status === "creating" || row.status === "completing") && (
                <div className="space-y-1">
                  <div className="w-full h-1 rounded-full overflow-hidden" style={{ backgroundColor: "var(--card-border)" }}>
                    <div className="h-full rounded-full transition-all" style={{ width: `${row.progress}%`, backgroundColor: "var(--tenant-primary, var(--brand-pink))" }} />
                  </div>
                  {row.status === "uploading" && (
                    <div className="flex items-center justify-between text-[10px]" style={{ color: "var(--text-muted)" }}>
                      <span>
                        {row.uploadState === "waiting-online"
                          ? t('pausedOffline')
                          : row.uploadState === "retrying"
                            ? t('retryingConn')
                            : `${row.progress}%`}
                      </span>
                      <span>{formatSpeedEta(row, t)}</span>
                    </div>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {error && (
        <div className="flex items-start gap-2 text-sm p-3 rounded-lg" style={{ backgroundColor: "rgba(239, 68, 68, 0.1)", color: "#ef4444" }}>
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <Button onClick={handleSubmitAll} disabled={submittableCount === 0 || isSubmitting} className="w-full"
        style={{ backgroundColor: submittableCount > 0 && !isSubmitting ? "var(--tenant-primary, var(--brand-pink))" : undefined, color: submittableCount > 0 && !isSubmitting ? "var(--primary-foreground)" : undefined }}>
        {isSubmitting ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Upload className="w-4 h-4 mr-2" />}
        {isSubmitting ? t('submitting') : t('submitAll', { count: submittableCount })}
      </Button>
    </div>
  )
}

// "3.2 MB/s · 2m 10s left" — empty until the engine has a throughput sample.
function formatSpeedEta(row: EditableRow, t: ReturnType<typeof useTranslations>): string {
  const parts: string[] = []
  if (row.bytesPerSecond && row.bytesPerSecond > 0) {
    parts.push(t('speedMBs', { mb: (row.bytesPerSecond / (1024 * 1024)).toFixed(1) }))
  }
  if (row.etaSeconds !== null && row.etaSeconds !== undefined && isFinite(row.etaSeconds)) {
    const total = Math.round(row.etaSeconds)
    const m = Math.floor(total / 60)
    const s = total % 60
    parts.push(m > 0 ? t('etaMinSec', { m, s }) : t('etaSec', { s }))
  }
  return parts.join(" · ")
}

function StatusBadge({ row, valid, t }: { row: EditableRow; valid: boolean; t: ReturnType<typeof useTranslations> }) {
  if (row.status === "done") {
    return <span className="flex items-center gap-1 text-green-500"><CheckCircle2 className="w-3.5 h-3.5" />{t('statusDone')}{row.jobId ? ` (${row.jobId.slice(0, 8)})` : ""}</span>
  }
  if (row.status === "error") {
    // Retryable — "Submit" will resume this row (reusing its job if one exists).
    return <span className="flex items-center gap-1 text-red-400 truncate"><AlertTriangle className="w-3.5 h-3.5 shrink-0" />{row.error || t('statusError')} · {t('willRetry')}</span>
  }
  if (row.status === "creating" || row.status === "uploading" || row.status === "completing") {
    return <span className="flex items-center gap-1"><Loader2 className="w-3.5 h-3.5 animate-spin" />{t('statusSubmitting')}</span>
  }
  if (!valid) {
    return <span className="text-red-400">{t('statusNeedsFix')}</span>
  }
  if (row.warning || row.confidence !== "high") {
    return <span style={{ color: "#eab308" }}>{t('statusCheck')}</span>
  }
  return <span>{t('statusReady')}</span>
}
