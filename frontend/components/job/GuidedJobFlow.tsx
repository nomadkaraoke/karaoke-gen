"use client"

import { useState } from "react"
import { useTranslations } from 'next-intl'
import { useAuth } from "@/lib/auth"
import { useTenant } from "@/lib/tenant"
import { api, ApiError } from "@/lib/api"
import { AlertTriangle, CheckCircle2, Music } from "lucide-react"
import { Button } from "@/components/ui/button"
import { BuyCreditsDialog } from "@/components/credits/BuyCreditsDialog"
import { SongInfoStep } from "./steps/SongInfoStep"
import { AudioSourceStep } from "./steps/AudioSourceStep"
import { VisibilityStep } from "./steps/VisibilityStep"
import { CustomizeStep } from "./steps/CustomizeStep"
import type { ColorOverrides } from "./steps/CustomizeStep"

interface GuidedJobFlowProps {
  onJobCreated: () => void
}

type Step = 1 | 2 | 3 | 4

const STEP_LABEL_KEYS = ["songInfo", "chooseAudio", "visibility", "customizeCreate"] as const

/**
 * Upload custom style assets to an already-created job.
 * Runs in the background after the success screen is shown — failures are logged but don't block.
 */
async function uploadStyleAssets(
  jobId: string,
  karaokeBackground: File | null,
  introBackground: File | null,
  colorOverrides: ColorOverrides,
) {
  const filesToUpload: Array<{ file: File; file_type: string; content_type: string }> = []

  if (karaokeBackground) {
    filesToUpload.push({
      file: karaokeBackground,
      file_type: "style_karaoke_background",
      content_type: karaokeBackground.type,
    })
  }
  if (introBackground) {
    filesToUpload.push({
      file: introBackground,
      file_type: "style_intro_background",
      content_type: introBackground.type,
    })
  }

  if (filesToUpload.length === 0 && !colorOverrides.artist_color && !colorOverrides.title_color) {
    return // Nothing to upload
  }

  try {
    // Step 1: Get signed upload URLs (if there are files)
    if (filesToUpload.length > 0) {
      const { upload_urls } = await api.getStyleUploadUrls(
        jobId,
        filesToUpload.map((f) => ({
          filename: f.file.name,
          content_type: f.content_type,
          file_type: f.file_type,
        }))
      )

      // Step 2: Upload files to GCS in parallel
      await Promise.all(
        upload_urls.map((urlInfo) => {
          const fileInfo = filesToUpload.find((f) => f.file_type === urlInfo.file_type)
          if (!fileInfo) return Promise.resolve()
          return api.uploadFileToSignedUrl(urlInfo.upload_url, fileInfo.file, urlInfo.content_type)
        })
      )
    }

    // Step 3: Finalize with uploaded file types + color overrides
    const uploadedFileTypes = filesToUpload.map((f) => f.file_type)
    const hasColorOverrides = colorOverrides.artist_color || colorOverrides.title_color ||
      colorOverrides.sung_lyrics_color || colorOverrides.unsung_lyrics_color
    await api.completeStyleUploads(
      jobId,
      uploadedFileTypes,
      hasColorOverrides ? colorOverrides : undefined
    )
  } catch (err) {
    // Log but don't block — the job will fall back to the default theme
    console.error("Failed to upload custom style assets:", err)
  }
}

export function GuidedJobFlow({ onJobCreated }: GuidedJobFlowProps) {
  const t = useTranslations('jobFlow')
  const { user } = useAuth()
  const { features } = useTenant()
  const isAdmin = user?.role === "admin"
  const noCredits = !isAdmin && user?.credits === 0

  const [step, setStep] = useState<Step>(1)
  const [showSuccess, setShowSuccess] = useState(false)
  const [showBuyCreditsDialog, setShowBuyCreditsDialog] = useState(false)

  // Form state (persists across steps)
  const [artist, setArtist] = useState("")
  const [title, setTitle] = useState("")
  const [displayArtist, setDisplayArtist] = useState("")
  const [displayTitle, setDisplayTitle] = useState("")
  const [isPrivate, setIsPrivate] = useState(false)
  const [requiresAudioEdit, setRequiresAudioEdit] = useState(false)

  // Custom style state
  const [karaokeBackground, setKaraokeBackground] = useState<File | null>(null)
  const [introBackground, setIntroBackground] = useState<File | null>(null)
  const [colorOverrides, setColorOverrides] = useState<ColorOverrides>({})

  // Job tracking
  const [jobId, setJobId] = useState<string | null>(null)
  const [audioSource, setAudioSource] = useState<"search" | "upload" | "url">("search")
  const [selectedResultIndex, setSelectedResultIndex] = useState<number | null>(null)
  const [searchSessionId, setSearchSessionId] = useState<string | null>(null)

  // Pending fallback data (URL/upload stored until after Steps 3-4)
  const [pendingUrl, setPendingUrl] = useState<string | null>(null)
  const [pendingFile, setPendingFile] = useState<File | null>(null)

  // Submission state
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState("")

  function handleSearchCompleted(sessionId: string) {
    setSearchSessionId(sessionId)
    setAudioSource("search")
  }

  function handleUrlReady(url: string) {
    setPendingUrl(url)
    setAudioSource("url")
    setStep(3)
  }

  function handleFileReady(file: File) {
    setPendingFile(file)
    setAudioSource("upload")
    setStep(3)
  }

  function handleArtistTitleCorrection(newArtist: string, newTitle: string) {
    setArtist(newArtist)
    setTitle(newTitle)
  }

  function handleSearchResultChosen(resultIndex: number) {
    // Just store the choice — job is created at Step 3 confirm
    setSelectedResultIndex(resultIndex)
    setStep(3)
  }

  async function handleConfirm() {
    setIsSubmitting(true)
    setSubmitError("")
    try {
      let createdJobId: string
      // For URL/upload, the artist/title params ARE the display values
      const effectiveArtist = displayArtist.trim() || artist.trim()
      const effectiveTitle = displayTitle.trim() || title.trim()

      if (audioSource === "url" && pendingUrl) {
        // URL fallback path — create job now with visibility from Step 3
        const response = await api.createJobFromUrl(pendingUrl, effectiveArtist, effectiveTitle, {
          is_private: isPrivate,
          requires_audio_edit: requiresAudioEdit || undefined,
        })
        createdJobId = response.job_id
      } else if (audioSource === "upload" && pendingFile) {
        // Upload fallback path — upload and create job now with visibility from Step 3
        const response = await api.uploadJobSmart(
          pendingFile, effectiveArtist, effectiveTitle,
          { is_private: isPrivate, requires_audio_edit: requiresAudioEdit || undefined },
        )
        createdJobId = response.job_id
      } else if (searchSessionId && selectedResultIndex !== null) {
        // Search path — create job from search session
        const response = await api.createJobFromSearch({
          search_session_id: searchSessionId,
          selection_index: selectedResultIndex,
          artist: artist,
          title: title,
          display_artist: displayArtist.trim() || undefined,
          display_title: displayTitle.trim() || undefined,
          is_private: isPrivate,
          requires_audio_edit: requiresAudioEdit || undefined,
        })
        createdJobId = response.job_id
      } else {
        setSubmitError(t('missingAudioSource'))
        return
      }

      setJobId(createdJobId)
      onJobCreated()
      setShowSuccess(true)

      // Upload style assets in the background (non-blocking)
      const hasStyleCustomizations = karaokeBackground || introBackground ||
        colorOverrides.artist_color || colorOverrides.title_color ||
        colorOverrides.sung_lyrics_color || colorOverrides.unsung_lyrics_color
      if (isPrivate && hasStyleCustomizations) {
        uploadStyleAssets(createdJobId, karaokeBackground, introBackground, colorOverrides)
      }
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.message.includes("Search expired") || (err.status === 404 && audioSource === "search")) {
          // Search session expired — clear it and send user back to re-search
          setSearchSessionId(null)
          setSelectedResultIndex(null)
          setStep(2)
          setSubmitError(t('searchExpired'))
        } else {
          setSubmitError(err.message)
        }
      } else {
        setSubmitError(t('failedToStartProcessing'))
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  function handleBackFromAudio() {
    // Sessions expire naturally — no cleanup needed
    setSearchSessionId(null)
    setAudioSource("search")
    setSelectedResultIndex(null)
    setPendingUrl(null)
    setPendingFile(null)
    setStep(1)
  }

  function resetFlow() {
    setStep(1)
    setShowSuccess(false)
    setArtist("")
    setTitle("")
    setDisplayArtist("")
    setDisplayTitle("")
    setIsPrivate(false)
    setRequiresAudioEdit(false)
    setKaraokeBackground(null)
    setIntroBackground(null)
    setColorOverrides({})
    setJobId(null)
    setAudioSource("search")
    setSelectedResultIndex(null)
    setSearchSessionId(null)
    setPendingUrl(null)
    setPendingFile(null)
    setSubmitError("")
  }

  // Success state
  if (showSuccess) {
    const effectiveArtist = displayArtist.trim() || artist
    const effectiveTitle = displayTitle.trim() || title

    return (
      <div className="space-y-5">
        <div className="flex flex-col items-center text-center py-5 space-y-3">
          <div
            className="w-12 h-12 rounded-full flex items-center justify-center"
            style={{ backgroundColor: 'rgba(34, 197, 94, 0.15)' }}
          >
            <CheckCircle2 className="w-7 h-7 text-green-500" />
          </div>
          <div>
            <h2 className="text-lg font-semibold" style={{ color: 'var(--text)' }}>
              {t('jobCreated')}
            </h2>
            <p className="text-sm mt-1" style={{ color: 'var(--text-muted)' }}>
              <span className="font-medium" style={{ color: 'var(--text)' }}>{effectiveArtist} - {effectiveTitle}</span>
            </p>
            {jobId && (
              <p className="text-xs mt-0.5 font-mono" style={{ color: 'var(--text-muted)' }} data-testid="created-job-id">
                ID: {jobId.slice(0, 8)}
              </p>
            )}
          </div>
        </div>

        {/* Timeline */}
        <p className="text-xs font-semibold uppercase tracking-wider" style={{ color: 'var(--text-muted)' }}>{t('whatsHappensNext')}</p>
        <div className="space-y-0 mt-3">
          {/* Step 1: Automated processing */}
          <div className="flex gap-3">
            <div className="flex flex-col items-center">
              <div className="w-8 h-8 rounded-full flex items-center justify-center shrink-0 bg-blue-500/15">
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" className="text-blue-400">
                  <path d="M8 1.5a1.25 1.25 0 0 1 1.177.825l.573 1.608 1.608.573a1.25 1.25 0 0 1 0 2.354l-1.608.573-.573 1.608a1.25 1.25 0 0 1-2.354 0l-.573-1.608-1.608-.573a1.25 1.25 0 0 1 0-2.354l1.608-.573.573-1.608A1.25 1.25 0 0 1 8 1.5Z" fill="currentColor" opacity="0.85"/>
                  <path d="M12 9a1 1 0 0 1 .94.66l.36 1.02 1.02.36a1 1 0 0 1 0 1.884l-1.02.36-.36 1.02a1 1 0 0 1-1.88 0l-.36-1.02-1.02-.36a1 1 0 0 1 0-1.884l1.02-.36.36-1.02A1 1 0 0 1 12 9Z" fill="currentColor" opacity="0.55"/>
                </svg>
              </div>
              <div className="w-px flex-1 min-h-[20px]" style={{ backgroundColor: 'var(--card-border)' }} />
            </div>
            <div className="pb-4 pt-1">
              <p className="text-sm font-medium" style={{ color: 'var(--text)' }}>{t('audioProcessing')}</p>
              <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
                {t('audioProcessingDesc')}
              </p>
              <span className="inline-block mt-1.5 text-[10px] px-1.5 py-0.5 rounded-full font-medium bg-blue-500/15 text-blue-400">
                {t('automatic')}
              </span>
            </div>
          </div>

          {/* Step 1.5: Audio editing (conditional) */}
          {requiresAudioEdit && (
            <div className="flex gap-3">
              <div className="flex flex-col items-center">
                <div className="w-8 h-8 rounded-full flex items-center justify-center shrink-0" style={{ backgroundColor: 'rgba(255, 122, 204, 0.15)' }}>
                  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" style={{ color: 'var(--brand-pink)' }}>
                    <path d="M13.5 3.5l-1-1L8 7l-1.5-1.5-1 1L8 9l5.5-5.5Z" fill="currentColor" opacity="0.85"/>
                    <path d="M2 12h12v1.5H2z" fill="currentColor" opacity="0.5"/>
                  </svg>
                </div>
                <div className="w-px flex-1 min-h-[20px]" style={{ backgroundColor: 'var(--card-border)' }} />
              </div>
              <div className="pb-4 pt-1">
                <p className="text-sm font-medium" style={{ color: 'var(--text)' }}>{t('youEditTheAudio')}</p>
                <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
                  {t('youEditDesc')}
                </p>
                <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                  <span className="inline-block text-[10px] px-1.5 py-0.5 rounded-full font-medium" style={{ backgroundColor: 'rgba(255, 122, 204, 0.15)', color: 'var(--brand-pink)' }}>
                    {t('you')}
                  </span>
                  <span className="flex items-center gap-1 text-[10px]" style={{ color: 'var(--text-muted)' }}>
                    <svg width="12" height="12" viewBox="0 0 16 16" fill="none">
                      <path d="M2 3.5A1.5 1.5 0 0 1 3.5 2h9A1.5 1.5 0 0 1 14 3.5v.401a1 1 0 0 1-.373.78L8.707 8.79a1 1 0 0 1-1.414 0L2.373 4.68A1 1 0 0 1 2 3.9V3.5Z" fill="currentColor" opacity="0.7"/>
                      <path d="M2 6.12l4.586 3.59a2 2 0 0 0 2.828 0L14 6.12V12.5a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 2 12.5V6.12Z" fill="currentColor" opacity="0.5"/>
                    </svg>
                    {t('emailChime')}
                  </span>
                </div>
              </div>
            </div>
          )}

          {/* Step 2: User review */}
          <div className="flex gap-3">
            <div className="flex flex-col items-center">
              <div className="w-8 h-8 rounded-full flex items-center justify-center shrink-0" style={{ backgroundColor: 'rgba(255, 122, 204, 0.15)' }}>
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" style={{ color: 'var(--brand-pink)' }}>
                  <path d="M8 2a3 3 0 1 0 0 6 3 3 0 0 0 0-6ZM3.5 13.5c0-2.485 2.015-4.5 4.5-4.5s4.5 2.015 4.5 4.5a.5.5 0 0 1-.5.5h-8a.5.5 0 0 1-.5-.5Z" fill="currentColor" opacity="0.85"/>
                </svg>
              </div>
              <div className="w-px flex-1 min-h-[20px]" style={{ backgroundColor: 'var(--card-border)' }} />
            </div>
            <div className="pb-4 pt-1">
              <p className="text-sm font-medium" style={{ color: 'var(--text)' }}>{t('youReviewLyrics')}</p>
              <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
                {t('youReviewDesc')}
              </p>
              <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                <span className="inline-block text-[10px] px-1.5 py-0.5 rounded-full font-medium" style={{ backgroundColor: 'rgba(255, 122, 204, 0.15)', color: 'var(--brand-pink)' }}>
                  {t('you')}
                </span>
                <span className="flex items-center gap-1 text-[10px]" style={{ color: 'var(--text-muted)' }}>
                  <svg width="12" height="12" viewBox="0 0 16 16" fill="none">
                    <path d="M2 3.5A1.5 1.5 0 0 1 3.5 2h9A1.5 1.5 0 0 1 14 3.5v.401a1 1 0 0 1-.373.78L8.707 8.79a1 1 0 0 1-1.414 0L2.373 4.68A1 1 0 0 1 2 3.9V3.5Z" fill="currentColor" opacity="0.7"/>
                    <path d="M2 6.12l4.586 3.59a2 2 0 0 0 2.828 0L14 6.12V12.5a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 2 12.5V6.12Z" fill="currentColor" opacity="0.5"/>
                  </svg>
                  {t('emailChime')}
                </span>
              </div>
            </div>
          </div>

          {/* Step 3: Final render */}
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
              <p className="text-sm font-medium" style={{ color: 'var(--text)' }}>{t('videoDelivered')}</p>
              <p className="text-xs mt-0.5" style={{ color: 'var(--text-muted)' }}>
                {t('videoDeliveredDesc')}
              </p>
              <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                <span className="inline-block text-[10px] px-1.5 py-0.5 rounded-full font-medium bg-green-500/15 text-green-400">
                  {t('automatic')}
                </span>
                <span className="flex items-center gap-1 text-[10px]" style={{ color: 'var(--text-muted)' }}>
                  <svg width="12" height="12" viewBox="0 0 16 16" fill="none">
                    <path d="M2 3.5A1.5 1.5 0 0 1 3.5 2h9A1.5 1.5 0 0 1 14 3.5v.401a1 1 0 0 1-.373.78L8.707 8.79a1 1 0 0 1-1.414 0L2.373 4.68A1 1 0 0 1 2 3.9V3.5Z" fill="currentColor" opacity="0.7"/>
                    <path d="M2 6.12l4.586 3.59a2 2 0 0 0 2.828 0L14 6.12V12.5a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 2 12.5V6.12Z" fill="currentColor" opacity="0.5"/>
                  </svg>
                  {t('emailWithLink')}
                </span>
              </div>
            </div>
          </div>
        </div>

        <p className="text-[10px] text-center" style={{ color: 'var(--text-muted)' }}>
          {t('trackProgress')}
        </p>

        <Button
          onClick={resetFlow}
          variant="outline"
          className="w-full"
          style={{ borderColor: 'var(--card-border)', color: 'var(--text)' }}
        >
          <Music className="w-4 h-4 mr-2" />
          {t('createAnother')}
        </Button>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Zero-credit warning banner */}
      {noCredits && (
        <div className="flex items-start gap-2 rounded-lg p-3 border border-amber-500/30 bg-amber-500/10">
          <AlertTriangle className="w-5 h-5 text-amber-500 shrink-0 mt-0.5" />
          <div className="text-sm" style={{ color: 'var(--text)' }}>
            <p>{t('noCreditsWarning')}</p>
            <button
              onClick={() => setShowBuyCreditsDialog(true)}
              className="inline-block mt-1 text-sm font-medium underline"
              style={{ color: 'var(--brand-pink)' }}
            >
              {t('buyCredits')}
            </button>
          </div>
        </div>
      )}

      {/* Step indicator */}
      <div className="flex items-center gap-1" role="navigation" aria-label="Progress">
        {STEP_LABEL_KEYS.map((key, i) => {
          const stepNum = (i + 1) as Step
          const isActive = step === stepNum
          const isCompleted = step > stepNum
          const label = t(`stepLabels.${key}`)

          return (
            <div key={key} className="flex items-center gap-1 flex-1">
              <div className="flex items-center gap-1.5 shrink-0">
                <div
                  className={`w-6 h-6 rounded-full flex items-center justify-center text-[10px] font-semibold shrink-0 transition-colors ${
                    isActive
                      ? 'bg-[var(--brand-pink)] text-white'
                      : isCompleted
                        ? 'bg-[var(--brand-pink)]/20 text-[var(--brand-pink)]'
                        : 'bg-[var(--secondary)] text-[var(--text-muted)]'
                  }`}
                >
                  {isCompleted ? "\u2713" : stepNum}
                </div>
                <span
                  className={`text-[10px] transition-colors hidden sm:inline ${
                    isActive ? 'font-medium' : ''
                  }`}
                  style={{ color: isActive ? 'var(--text)' : 'var(--text-muted)' }}
                >
                  {label}
                </span>
              </div>
              {i < STEP_LABEL_KEYS.length - 1 && (
                <div
                  className="h-px flex-1 mx-1 hidden sm:block"
                  style={{
                    backgroundColor: isCompleted ? 'var(--brand-pink)' : 'var(--card-border)',
                    opacity: isCompleted ? 0.4 : 1,
                  }}
                />
              )}
            </div>
          )
        })}
      </div>

      {/* Submit error */}
      {submitError && (
        <div className="text-sm text-red-400 bg-red-500/10 rounded p-3">
          <p>{submitError}</p>
        </div>
      )}

      {/* Step content */}
      {step === 1 && (
        <SongInfoStep
          artist={artist}
          title={title}
          onArtistChange={setArtist}
          onTitleChange={setTitle}
          onNext={() => setStep(2)}
          disabled={noCredits}
        />
      )}

      {step === 2 && (
        <AudioSourceStep
          artist={artist}
          title={title}
          onSearchCompleted={handleSearchCompleted}
          onSearchResultChosen={handleSearchResultChosen}
          onArtistTitleCorrection={handleArtistTitleCorrection}
          onUrlReady={handleUrlReady}
          onFileReady={handleFileReady}
          onBack={handleBackFromAudio}
          noCredits={noCredits}
          onAudioEditChange={setRequiresAudioEdit}
        />
      )}

      {step === 3 && (
        <VisibilityStep
          isPrivate={isPrivate}
          onPrivateChange={setIsPrivate}
          onNext={() => setStep(4)}
          onBack={() => setStep(2)}
        />
      )}

      {step === 4 && (
        <CustomizeStep
          artist={artist}
          title={title}
          displayArtist={displayArtist}
          displayTitle={displayTitle}
          onDisplayArtistChange={setDisplayArtist}
          onDisplayTitleChange={setDisplayTitle}
          isPrivate={isPrivate}
          karaokeBackground={karaokeBackground}
          onKaraokeBackgroundChange={setKaraokeBackground}
          introBackground={introBackground}
          onIntroBackgroundChange={setIntroBackground}
          colorOverrides={colorOverrides}
          onColorOverridesChange={setColorOverrides}
          onConfirm={handleConfirm}
          onBack={() => setStep(3)}
          isSubmitting={isSubmitting}
        />
      )}

      <BuyCreditsDialog open={showBuyCreditsDialog} onClose={() => setShowBuyCreditsDialog(false)} />
    </div>
  )
}
