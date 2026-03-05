"use client"

import { useState, useRef, useCallback } from "react"
import { api, ApiError } from "@/lib/api"
import { useTenant } from "@/lib/tenant"
import { CheckCircle2, Upload, Music, Loader2, AlertTriangle } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

const ACCEPTED_AUDIO_TYPES = ".mp3,.wav,.flac,.m4a,.ogg,.aac"
const ACCEPTED_AUDIO_MIMES = [
  "audio/mpeg", "audio/wav", "audio/x-wav", "audio/flac",
  "audio/mp4", "audio/m4a", "audio/ogg", "audio/aac",
  "application/octet-stream", // fallback for unrecognized
]

interface TenantJobFlowProps {
  onJobCreated: () => void
}

type SubmitPhase = "idle" | "creating" | "uploading-mixed" | "uploading-instrumental" | "completing" | "done"

export function TenantJobFlow({ onJobCreated }: TenantJobFlowProps) {
  const { branding } = useTenant()

  // Form fields
  const [artist, setArtist] = useState("")
  const [title, setTitle] = useState("")
  const [mixedFile, setMixedFile] = useState<File | null>(null)
  const [instrumentalFile, setInstrumentalFile] = useState<File | null>(null)

  // Submission state
  const [phase, setPhase] = useState<SubmitPhase>("idle")
  const [uploadProgress, setUploadProgress] = useState(0)
  const [error, setError] = useState("")
  const [jobId, setJobId] = useState<string | null>(null)

  // Refs for file inputs
  const mixedInputRef = useRef<HTMLInputElement>(null)
  const instrumentalInputRef = useRef<HTMLInputElement>(null)

  const canSubmit = artist.trim() && title.trim() && mixedFile && instrumentalFile && phase === "idle"

  const resetFlow = useCallback(() => {
    setArtist("")
    setTitle("")
    setMixedFile(null)
    setInstrumentalFile(null)
    setPhase("idle")
    setUploadProgress(0)
    setError("")
    setJobId(null)
    // Clear file inputs
    if (mixedInputRef.current) mixedInputRef.current.value = ""
    if (instrumentalInputRef.current) instrumentalInputRef.current.value = ""
  }, [])

  async function handleSubmit() {
    if (!canSubmit || !mixedFile || !instrumentalFile) return

    setError("")
    setPhase("creating")

    try {
      // Step 1: Create job with upload URLs
      const files = [
        {
          filename: mixedFile.name,
          content_type: mixedFile.type || "application/octet-stream",
          file_type: "audio",
        },
        {
          filename: instrumentalFile.name,
          content_type: instrumentalFile.type || "application/octet-stream",
          file_type: "existing_instrumental",
        },
      ]

      const createResponse = await api.createJobWithUploadUrls(
        artist.trim(),
        title.trim(),
        files,
        { is_private: true, existing_instrumental: true },
      )

      setJobId(createResponse.job_id)

      // Step 2: Upload mixed audio
      setPhase("uploading-mixed")
      const audioUrl = createResponse.upload_urls.find(u => u.file_type === "audio")
      if (!audioUrl) throw new ApiError("No upload URL for audio file", 500)

      await api.uploadToSignedUrl(
        audioUrl.upload_url,
        mixedFile,
        audioUrl.content_type,
        (loaded, total) => setUploadProgress(Math.round((loaded / total) * 50)),
      )

      // Step 3: Upload instrumental
      setPhase("uploading-instrumental")
      const instrumentalUrl = createResponse.upload_urls.find(u => u.file_type === "existing_instrumental")
      if (!instrumentalUrl) throw new ApiError("No upload URL for instrumental file", 500)

      await api.uploadToSignedUrl(
        instrumentalUrl.upload_url,
        instrumentalFile,
        instrumentalUrl.content_type,
        (loaded, total) => setUploadProgress(50 + Math.round((loaded / total) * 50)),
      )

      // Step 4: Complete upload
      setPhase("completing")
      await api.completeJobUpload(createResponse.job_id, ["audio", "existing_instrumental"])

      setPhase("done")
      onJobCreated()
    } catch (err: any) {
      const message = err instanceof ApiError
        ? err.message
        : "Something went wrong. Please try again."
      setError(message)
      setPhase("idle")
      setUploadProgress(0)
    }
  }

  // Success view
  if (phase === "done") {
    return (
      <div className="space-y-5">
        <div className="flex flex-col items-center text-center py-5 space-y-3">
          <div
            className="w-12 h-12 rounded-full flex items-center justify-center"
            style={{ backgroundColor: "rgba(34, 197, 94, 0.15)" }}
          >
            <CheckCircle2 className="w-7 h-7 text-green-500" />
          </div>
          <div>
            <h2 className="text-lg font-semibold" style={{ color: "var(--text)" }}>
              Track Submitted
            </h2>
            <p className="text-sm mt-1" style={{ color: "var(--text-muted)" }}>
              <span className="font-medium" style={{ color: "var(--text)" }}>
                {artist} - {title}
              </span>
            </p>
            {jobId && (
              <p className="text-xs mt-0.5 font-mono" style={{ color: "var(--text-muted)" }} data-testid="created-job-id">
                ID: {jobId.slice(0, 8)}
              </p>
            )}
          </div>
        </div>

        {/* Timeline — simplified for tenant (no audio processing step) */}
        <p className="text-xs font-semibold uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>
          What happens next
        </p>
        <div className="space-y-0 mt-3">
          {/* Step 1: Lyrics transcription */}
          <div className="flex gap-3">
            <div className="flex flex-col items-center">
              <div className="w-8 h-8 rounded-full flex items-center justify-center shrink-0 bg-blue-500/15">
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" className="text-blue-400">
                  <path d="M8 1.5a1.25 1.25 0 0 1 1.177.825l.573 1.608 1.608.573a1.25 1.25 0 0 1 0 2.354l-1.608.573-.573 1.608a1.25 1.25 0 0 1-2.354 0l-.573-1.608-1.608-.573a1.25 1.25 0 0 1 0-2.354l1.608-.573.573-1.608A1.25 1.25 0 0 1 8 1.5Z" fill="currentColor" opacity="0.85"/>
                  <path d="M12 9a1 1 0 0 1 .94.66l.36 1.02 1.02.36a1 1 0 0 1 0 1.884l-1.02.36-.36 1.02a1 1 0 0 1-1.88 0l-.36-1.02-1.02-.36a1 1 0 0 1 0-1.884l1.02-.36.36-1.02A1 1 0 0 1 12 9Z" fill="currentColor" opacity="0.55"/>
                </svg>
              </div>
              <div className="w-px flex-1 min-h-[20px]" style={{ backgroundColor: "var(--card-border)" }} />
            </div>
            <div className="pb-4 pt-1">
              <p className="text-sm font-medium" style={{ color: "var(--text)" }}>Lyrics transcription</p>
              <p className="text-xs mt-0.5" style={{ color: "var(--text-muted)" }}>
                We transcribe and sync lyrics from your audio. <span className="font-medium" style={{ color: "var(--text)" }}>~5 minutes</span>
              </p>
              <span className="inline-block mt-1.5 text-[10px] px-1.5 py-0.5 rounded-full font-medium bg-blue-500/15 text-blue-400">
                Automatic
              </span>
            </div>
          </div>

          {/* Step 2: User review */}
          <div className="flex gap-3">
            <div className="flex flex-col items-center">
              <div className="w-8 h-8 rounded-full flex items-center justify-center shrink-0" style={{ backgroundColor: "rgba(255, 122, 204, 0.15)" }}>
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" style={{ color: "var(--brand-pink)" }}>
                  <path d="M8 2a3 3 0 1 0 0 6 3 3 0 0 0 0-6ZM3.5 13.5c0-2.485 2.015-4.5 4.5-4.5s4.5 2.015 4.5 4.5a.5.5 0 0 1-.5.5h-8a.5.5 0 0 1-.5-.5Z" fill="currentColor" opacity="0.85"/>
                </svg>
              </div>
              <div className="w-px flex-1 min-h-[20px]" style={{ backgroundColor: "var(--card-border)" }} />
            </div>
            <div className="pb-4 pt-1">
              <p className="text-sm font-medium" style={{ color: "var(--text)" }}>Review lyrics</p>
              <p className="text-xs mt-0.5" style={{ color: "var(--text-muted)" }}>
                Check the synced lyrics and fix any errors.
              </p>
              <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                <span className="inline-block text-[10px] px-1.5 py-0.5 rounded-full font-medium" style={{ backgroundColor: "rgba(255, 122, 204, 0.15)", color: "var(--brand-pink)" }}>
                  You
                </span>
                <span className="flex items-center gap-1 text-[10px]" style={{ color: "var(--text-muted)" }}>
                  <svg width="12" height="12" viewBox="0 0 16 16" fill="none">
                    <path d="M2 3.5A1.5 1.5 0 0 1 3.5 2h9A1.5 1.5 0 0 1 14 3.5v.401a1 1 0 0 1-.373.78L8.707 8.79a1 1 0 0 1-1.414 0L2.373 4.68A1 1 0 0 1 2 3.9V3.5Z" fill="currentColor" opacity="0.7"/>
                    <path d="M2 6.12l4.586 3.59a2 2 0 0 0 2.828 0L14 6.12V12.5a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 2 12.5V6.12Z" fill="currentColor" opacity="0.5"/>
                  </svg>
                  Email when ready
                </span>
              </div>
            </div>
          </div>

          {/* Step 3: Video delivered */}
          <div className="flex gap-3">
            <div className="flex flex-col items-center">
              <div className="w-8 h-8 rounded-full flex items-center justify-center shrink-0 bg-green-500/15">
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" className="text-green-400">
                  <path d="M3 2.5A1.5 1.5 0 0 1 4.5 1h7A1.5 1.5 0 0 1 13 2.5v11a.5.5 0 0 1-.765.424L8 11.28l-4.235 2.644A.5.5 0 0 1 3 13.5v-11Z" fill="currentColor" opacity="0.5"/>
                  <path d="M6.354 7.854 7.5 9l2.146-2.146a.5.5 0 0 0-.708-.708L7.5 7.586l-.646-.647a.5.5 0 1 0-.708.708l.208.207Z" fill="currentColor"/>
                </svg>
              </div>
            </div>
            <div className="pt-1">
              <p className="text-sm font-medium" style={{ color: "var(--text)" }}>Video delivered</p>
              <p className="text-xs mt-0.5" style={{ color: "var(--text-muted)" }}>
                After approval, your karaoke video is rendered and delivered. <span className="font-medium" style={{ color: "var(--text)" }}>~10 minutes</span>
              </p>
              <span className="inline-block mt-1.5 text-[10px] px-1.5 py-0.5 rounded-full font-medium bg-green-500/15 text-green-400">
                Automatic
              </span>
            </div>
          </div>
        </div>

        <p className="text-[10px] text-center" style={{ color: "var(--text-muted)" }}>
          Track progress in the <span className="font-medium" style={{ color: "var(--text)" }}>Recent Jobs</span> list.
        </p>

        <Button
          onClick={resetFlow}
          variant="outline"
          className="w-full"
          style={{ borderColor: "var(--card-border)", color: "var(--text)" }}
        >
          <Music className="w-4 h-4 mr-2" />
          Submit Another Track
        </Button>
      </div>
    )
  }

  const isSubmitting = phase !== "idle"

  return (
    <div className="space-y-4">
      {/* Artist */}
      <div className="space-y-1.5">
        <Label htmlFor="tenant-artist" style={{ color: "var(--text)" }}>Artist</Label>
        <Input
          id="tenant-artist"
          placeholder="e.g. Adele"
          value={artist}
          onChange={e => setArtist(e.target.value)}
          disabled={isSubmitting}
          style={{ borderColor: "var(--card-border)", color: "var(--text)", backgroundColor: "var(--input)" }}
        />
      </div>

      {/* Title */}
      <div className="space-y-1.5">
        <Label htmlFor="tenant-title" style={{ color: "var(--text)" }}>Title</Label>
        <Input
          id="tenant-title"
          placeholder="e.g. Someone Like You"
          value={title}
          onChange={e => setTitle(e.target.value)}
          disabled={isSubmitting}
          style={{ borderColor: "var(--card-border)", color: "var(--text)", backgroundColor: "var(--input)" }}
        />
      </div>

      {/* Mixed Audio */}
      <FileDropZone
        id="tenant-mixed-audio"
        label="Mixed Audio"
        hint="The full song with vocals"
        file={mixedFile}
        onFileSelect={setMixedFile}
        inputRef={mixedInputRef}
        disabled={isSubmitting}
        accept={ACCEPTED_AUDIO_TYPES}
      />

      {/* Instrumental Audio */}
      <FileDropZone
        id="tenant-instrumental-audio"
        label="Instrumental Audio"
        hint="The instrumental (backing track)"
        file={instrumentalFile}
        onFileSelect={setInstrumentalFile}
        inputRef={instrumentalInputRef}
        disabled={isSubmitting}
        accept={ACCEPTED_AUDIO_TYPES}
      />

      {/* Error */}
      {error && (
        <div className="flex items-start gap-2 text-sm p-3 rounded-lg" style={{ backgroundColor: "rgba(239, 68, 68, 0.1)", color: "#ef4444" }}>
          <AlertTriangle className="w-4 h-4 mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Progress */}
      {isSubmitting && (
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-sm" style={{ color: "var(--text-muted)" }}>
            <Loader2 className="w-4 h-4 animate-spin" />
            <span>
              {phase === "creating" && "Creating job..."}
              {phase === "uploading-mixed" && "Uploading mixed audio..."}
              {phase === "uploading-instrumental" && "Uploading instrumental..."}
              {phase === "completing" && "Finalizing..."}
            </span>
          </div>
          {(phase === "uploading-mixed" || phase === "uploading-instrumental") && (
            <div className="w-full h-1.5 rounded-full overflow-hidden" style={{ backgroundColor: "var(--card-border)" }}>
              <div
                className="h-full rounded-full transition-all duration-300"
                style={{
                  width: `${uploadProgress}%`,
                  backgroundColor: "var(--tenant-primary, var(--brand-pink))",
                }}
              />
            </div>
          )}
        </div>
      )}

      {/* Submit */}
      <Button
        onClick={handleSubmit}
        disabled={!canSubmit}
        className="w-full"
        style={{
          backgroundColor: canSubmit ? "var(--tenant-primary, var(--brand-pink))" : undefined,
          color: canSubmit ? "var(--primary-foreground)" : undefined,
        }}
      >
        <Upload className="w-4 h-4 mr-2" />
        Submit Track
      </Button>
    </div>
  )
}

// ============================================================================
// File drop zone sub-component
// ============================================================================

interface FileDropZoneProps {
  id: string
  label: string
  hint: string
  file: File | null
  onFileSelect: (file: File | null) => void
  inputRef: React.RefObject<HTMLInputElement | null>
  disabled: boolean
  accept: string
}

function FileDropZone({ id, label, hint, file, onFileSelect, inputRef, disabled, accept }: FileDropZoneProps) {
  const [isDragOver, setIsDragOver] = useState(false)

  function handleDrop(e: React.DragEvent) {
    e.preventDefault()
    setIsDragOver(false)
    if (disabled) return
    const droppedFile = e.dataTransfer.files[0]
    if (droppedFile) onFileSelect(droppedFile)
  }

  function handleDragOver(e: React.DragEvent) {
    e.preventDefault()
    if (!disabled) setIsDragOver(true)
  }

  return (
    <div className="space-y-1.5">
      <Label htmlFor={id} style={{ color: "var(--text)" }}>{label}</Label>
      <div
        className={`relative border-2 border-dashed rounded-lg p-4 text-center cursor-pointer transition-colors ${
          disabled ? "opacity-50 cursor-not-allowed" : "hover:border-white/30"
        }`}
        style={{
          borderColor: isDragOver ? "var(--tenant-primary, var(--brand-pink))" : "var(--card-border)",
          backgroundColor: isDragOver ? "rgba(255, 255, 255, 0.03)" : "transparent",
        }}
        onClick={() => !disabled && inputRef.current?.click()}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onDragLeave={() => setIsDragOver(false)}
      >
        <input
          ref={inputRef}
          id={id}
          type="file"
          accept={accept}
          className="hidden"
          disabled={disabled}
          onChange={e => {
            const f = e.target.files?.[0]
            if (f) onFileSelect(f)
          }}
        />
        {file ? (
          <div className="flex items-center justify-center gap-2">
            <Music className="w-4 h-4 shrink-0" style={{ color: "var(--tenant-primary, var(--brand-pink))" }} />
            <span className="text-sm truncate" style={{ color: "var(--text)" }}>{file.name}</span>
            <span className="text-xs shrink-0" style={{ color: "var(--text-muted)" }}>
              ({(file.size / (1024 * 1024)).toFixed(1)} MB)
            </span>
          </div>
        ) : (
          <div>
            <Upload className="w-5 h-5 mx-auto mb-1" style={{ color: "var(--text-muted)" }} />
            <p className="text-sm" style={{ color: "var(--text-muted)" }}>
              Drop file here or <span style={{ color: "var(--tenant-primary, var(--brand-pink))" }}>browse</span>
            </p>
            <p className="text-xs mt-0.5" style={{ color: "var(--text-muted)" }}>{hint}</p>
          </div>
        )}
      </div>
    </div>
  )
}
