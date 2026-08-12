/**
 * Job Status Display Utilities
 *
 * Maps backend job statuses to user-friendly step-based progress indicators.
 * The 10-step system simplifies 28+ backend statuses for better user comprehension.
 */

import type { Job } from "./api";

/**
 * Returns true if a Cloud Run Job auto-retry is currently expected for this job.
 *
 * Backed by `state_data.cloud_run_retry_pending.expires_at` written by the
 * audio-download worker when it fails on a non-final task attempt. Used to
 * show "Retrying automatically..." instead of a Retry button so users don't
 * race the system by clicking Retry during the auto-retry window.
 */
export function isAutoRetryPending(job: Pick<Job, 'state_data'>): boolean {
  const pending = job.state_data?.cloud_run_retry_pending as
    | { expires_at?: string }
    | undefined;
  if (!pending?.expires_at) return false;
  const expiresAt = Date.parse(pending.expires_at);
  if (Number.isNaN(expiresAt)) return false;
  return expiresAt > Date.now();
}

/**
 * Returns true when a job is stuck waiting for GCE encoding-worker capacity.
 *
 * The canonical signal is the `render_pending_capacity` status (parked, auto-
 * retried every 5 min). During the retry cycle the status briefly flips to
 * `review_complete`/`rendering_video` while an attempt is in flight, so we also
 * treat those as "waiting" when the persistent `state_data.render_pending_capacity`
 * breadcrumb (attempt_count >= 1) is present. Scoped to the render step so the
 * lingering breadcrumb never shows the note after the render succeeds and the
 * job moves on to encoding/packaging/complete.
 */
export function isWaitingForEncodingCapacity(job: Pick<Job, 'status' | 'state_data'>): boolean {
  const status = job.status?.toLowerCase() || "";
  if (status === "render_pending_capacity") return true;
  if (status === "review_complete" || status === "rendering_video") {
    const meta = job.state_data?.render_pending_capacity as
      | { attempt_count?: number }
      | undefined;
    return !!meta && Number(meta.attempt_count ?? 0) >= 1;
  }
  return false;
}

/**
 * Returns true while a private -> public visibility change is re-rendering and
 * republishing the track. Backed by `state_data.visibility_change_in_progress`,
 * set synchronously when the change starts and cleared when the job completes.
 * The track's outputs (finals, distribution links) are deleted for the duration,
 * so download buttons must be suppressed and the user told to wait (~15-30 min).
 */
export function isVisibilityChangeInProgress(job: Pick<Job, 'state_data'>): boolean {
  return !!job.state_data?.visibility_change_in_progress;
}

export interface JobStep {
  step: number;
  total: number;
  /** Translation key(s) from the "jobStatus" namespace. May contain " + " for combined parallel status labels. */
  label: string;
  isBlocking: boolean;
  color: string;
}

/**
 * Status-to-step mapping configuration.
 *
 * Steps are organized as:
 * 1. Setup
 * 2. Audio Search (optional)
 * 3. Download
 * 4. Processing (Audio + Lyrics in parallel)
 * 5. Screen Generation
 * 6. Lyrics Review (BLOCKING)
 * 7. Video Rendering
 * 8. Instrumental Selection (BLOCKING)
 * 9. Final Encoding
 * 10. Distribution / Complete
 */
export const STATUS_CONFIG: Record<
  string,
  { step: number; label: string; isBlocking: boolean; color: string }
> = {
  // Step 1: Setup
  pending: { step: 1, label: "settingUp", isBlocking: false, color: "text-muted-foreground" },

  // Step 2: Audio Search (optional path)
  searching_audio: { step: 2, label: "searchingForAudio", isBlocking: false, color: "text-blue-400" },
  awaiting_audio_selection: { step: 2, label: "selectAudioSource", isBlocking: true, color: "text-amber-400" },

  // Step 3: Download
  downloading_audio: { step: 3, label: "downloadingAudio", isBlocking: false, color: "text-blue-400" },
  // Transient torrent-download issue (rare track / few seeders / tracker blip);
  // auto-retried for up to 24h by the recover-stuck cron. Not blocking — no user action.
  download_pending_retry: { step: 3, label: "findingAudioSources", isBlocking: false, color: "text-amber-400" },
  downloading: { step: 3, label: "downloading", isBlocking: false, color: "text-blue-400" },

  // Step 3.5: Audio Editing (optional, BLOCKING)
  awaiting_audio_edit: { step: 3, label: "editAudio", isBlocking: true, color: "text-amber-400" },
  in_audio_edit: { step: 3, label: "editingAudio", isBlocking: true, color: "text-blue-400" },
  audio_edit_complete: { step: 3, label: "audioEdited", isBlocking: false, color: "text-teal-400" },

  // Step 3.5: Duration Pricing Confirmation (BLOCKING - user must confirm cost before heavy work)
  awaiting_duration_confirm: { step: 3, label: "confirmDurationCost", isBlocking: true, color: "text-amber-400" },

  // Step 4: Parallel Processing (Audio + Lyrics)
  separating_stage1: { step: 4, label: "separatingAudio1", isBlocking: false, color: "text-purple-400" },
  separating_stage2: { step: 4, label: "separatingAudio2", isBlocking: false, color: "text-purple-400" },
  audio_complete: { step: 4, label: "audioReadyProcessingLyrics", isBlocking: false, color: "text-purple-400" },
  transcribing: { step: 4, label: "transcribingLyrics", isBlocking: false, color: "text-blue-400" },
  correcting: { step: 4, label: "correctingLyrics", isBlocking: false, color: "text-blue-400" },
  lyrics_complete: { step: 4, label: "lyricsReadyProcessingAudio", isBlocking: false, color: "text-teal-400" },

  // Step 5: Screen Generation
  generating_screens: { step: 5, label: "generatingScreens", isBlocking: false, color: "text-cyan-400" },
  applying_padding: { step: 5, label: "syncingCountdown", isBlocking: false, color: "text-cyan-400" },

  // Step 6: Review (BLOCKING - requires user action)
  awaiting_review: { step: 6, label: "reviewLyrics", isBlocking: true, color: "text-amber-400" },
  in_review: { step: 6, label: "inReview", isBlocking: true, color: "text-blue-400" },

  // Step 7: Video Rendering
  review_complete: { step: 7, label: "startingRender", isBlocking: false, color: "text-teal-400" },
  rendering_video: { step: 7, label: "renderingVideo", isBlocking: false, color: "text-indigo-400" },
  // Parked waiting for GCE encoding capacity (or re-parked after a mid-render
  // stall); auto-retried by the scheduler. Not blocking — no user action.
  render_pending_capacity: { step: 7, label: "waitingForCapacity", isBlocking: false, color: "text-amber-400" },

  // Step 8: Instrumental Selection (BLOCKING - requires user action)
  awaiting_instrumental_selection: { step: 8, label: "selectInstrumental", isBlocking: true, color: "text-amber-400" },

  // Step 9: Final Encoding
  instrumental_selected: { step: 9, label: "startingFinalEncode", isBlocking: false, color: "text-pink-400" },
  generating_video: { step: 9, label: "generatingFinalVideo", isBlocking: false, color: "text-violet-400" },
  encoding: { step: 9, label: "encodingVideo", isBlocking: false, color: "text-violet-400" },
  packaging: { step: 9, label: "packagingFiles", isBlocking: false, color: "text-violet-400" },

  // Step 10: Distribution / Complete
  uploading: { step: 10, label: "uploading", isBlocking: false, color: "text-green-400" },
  notifying: { step: 10, label: "sendingNotifications", isBlocking: false, color: "text-green-400" },
  complete: { step: 10, label: "complete", isBlocking: false, color: "text-green-400" },
  prep_complete: { step: 10, label: "prepComplete", isBlocking: false, color: "text-green-400" },

  // Terminal states (no step progression)
  failed: { step: 0, label: "failed", isBlocking: false, color: "text-red-400" },
  cancelled: { step: 0, label: "cancelled", isBlocking: false, color: "text-muted-foreground" },
};

const TOTAL_STEPS = 10;

/**
 * Check if parallel workers (audio/lyrics) are actively running.
 * This is indicated by state_data containing audio_progress or lyrics_progress.
 */
function isParallelProcessingActive(job: Job): boolean {
  if (!job.state_data) return false;

  const audioProgress = job.state_data.audio_progress as { stage?: string } | undefined;
  const lyricsProgress = job.state_data.lyrics_progress as { stage?: string } | undefined;

  // Workers are active if we have progress data with a stage
  return !!(audioProgress?.stage || lyricsProgress?.stage);
}

/**
 * Get the step information for a job based on its status.
 *
 * @param job - The job object with status and optional state_data
 * @returns Step information including step number, label, and display properties
 */
export function getJobStep(job: Job): JobStep {
  const status = job.status?.toLowerCase() || "pending";
  const config = STATUS_CONFIG[status];

  if (!config) {
    // Unknown status - show generic processing state
    return {
      step: 0,
      total: TOTAL_STEPS,
      label: status.replace(/_/g, " "),
      isBlocking: false,
      color: "text-muted-foreground",
    };
  }

  // Special case: "downloading" status but parallel workers are actually running.
  // The backend sets status to "downloading" when audio download completes and workers start,
  // but doesn't update to step 4 statuses until screens_worker runs.
  // Show step 4 with detailed progress to avoid appearing "stuck" at downloading.
  if (status === "downloading" && isParallelProcessingActive(job)) {
    const enhancedLabel = getParallelProcessingLabel(job, "Processing");
    return {
      step: 4,
      total: TOTAL_STEPS,
      label: enhancedLabel,
      isBlocking: false,
      color: "text-purple-400", // Same as step 4 processing color
    };
  }

  // During parallel processing (step 4), check state_data for more detail
  if (config.step === 4 && job.state_data) {
    const enhancedLabel = getParallelProcessingLabel(job, config.label);
    return {
      step: config.step,
      total: TOTAL_STEPS,
      label: enhancedLabel,
      isBlocking: config.isBlocking,
      color: config.color,
    };
  }

  return {
    step: config.step,
    total: TOTAL_STEPS,
    label: config.label,
    isBlocking: config.isBlocking,
    color: config.color,
  };
}

/**
 * Get enhanced label for parallel processing stage.
 * Combines audio and lyrics progress when both are available.
 */
function getParallelProcessingLabel(job: Job, defaultLabel: string): string {
  const audioProgress = job.state_data?.audio_progress as
    | { stage?: string; message?: string }
    | undefined;
  const lyricsProgress = job.state_data?.lyrics_progress as
    | { stage?: string; message?: string }
    | undefined;

  // If we have both progress indicators, show combined status
  if (audioProgress && lyricsProgress) {
    const audioStage = audioProgress.stage || "";
    const lyricsStage = lyricsProgress.stage || "";

    const audioDone = audioStage === "audio_complete" || job.state_data?.audio_complete;
    const lyricsDone = lyricsStage === "lyrics_complete" || job.state_data?.lyrics_complete;

    if (audioDone && lyricsDone) {
      return "processingComplete";
    }

    // Show what's still running
    const parts: string[] = [];
    if (!audioDone) {
      parts.push(getShortAudioStatus(audioStage));
    }
    if (!lyricsDone) {
      parts.push(getShortLyricsStatus(lyricsStage));
    }

    if (parts.length > 0) {
      return parts.join(" + ");
    }
  }

  return defaultLabel;
}

function getShortAudioStatus(stage: string): string {
  switch (stage) {
    case "separating_stage1":
      return "audio1of2";
    case "separating_stage2":
      return "audio2of2";
    case "audio_complete":
      return "audioDone";
    default:
      return "audio";
  }
}

function getShortLyricsStatus(stage: string): string {
  switch (stage) {
    case "transcribing":
      return "transcribing";
    case "correcting":
      return "correcting";
    case "lyrics_complete":
      return "lyricsDone";
    default:
      return "lyrics";
  }
}

/**
 * Format a step indicator string like "[4/10] Processing..."
 *
 * @param step - Current step number (0 for terminal states)
 * @param total - Total number of steps
 * @param label - Human-readable label
 * @returns Formatted string
 */
export function formatStepIndicator(step: number, total: number, label: string): string {
  if (step === 0) {
    // Terminal states (failed, cancelled, etc.) don't show step numbers
    return label;
  }
  return `[${step}/${total}] ${label}`;
}

/**
 * Check if a job status requires user action.
 *
 * @param status - The job status string
 * @returns true if the status is a blocking state requiring user action
 */
export function isBlockingStatus(status: string): boolean {
  const config = STATUS_CONFIG[status?.toLowerCase()];
  return config?.isBlocking ?? false;
}

/**
 * Check if a blocking status should trigger a notification (chime + title flash).
 *
 * Excludes `awaiting_audio_selection` because the user is already engaged
 * with the inline audio picker during job creation — no chime needed.
 *
 * @param status - The job status string
 * @returns true if the status is blocking AND warrants a notification
 */
export function isNotifiableBlockingStatus(status: string): boolean {
  const normalized = status?.toLowerCase()
  if (normalized === 'awaiting_audio_selection') return false
  return isBlockingStatus(status)
}

/**
 * Get the progress percentage for a job (0-100).
 * This is based on step progression, not the backend progress field.
 *
 * @param job - The job object
 * @returns Progress percentage (0-100)
 */
export function getJobProgressPercent(job: Job): number {
  const { step, total } = getJobStep(job);
  if (step === 0 || total === 0) return 0;
  return Math.round((step / total) * 100);
}

/**
 * Sort jobs by creation date (newest first).
 */
export function sortJobsByDate(jobs: Job[]): Job[] {
  return [...jobs].sort((a, b) => {
    return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
  });
}

/**
 * Whether a job should render as a standalone card on the main dashboard.
 *
 * Self-service jobs at `awaiting_audio_selection` are driven by the guided-flow
 * wizard in the same browser session and must NOT appear as standalone cards
 * (the wizard owns them). Made-for-you orders also sit at that status, but they
 * are created server-side by the Stripe webhook with no active guided-flow
 * session — so they'd otherwise be invisible to an admin. Keep those visible so
 * their built-in "Open Audio Selection" action is reachable from the dashboard.
 */
export function shouldShowJobOnDashboard(job: Pick<Job, 'status' | 'made_for_you'>): boolean {
  return job.status !== 'awaiting_audio_selection' || Boolean(job.made_for_you);
}

