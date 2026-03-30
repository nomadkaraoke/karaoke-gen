/**
 * Job Status Display Utilities
 *
 * Maps backend job statuses to user-friendly step-based progress indicators.
 * The 10-step system simplifies 28+ backend statuses for better user comprehension.
 */

import type { Job } from "./api";

export interface JobStep {
  step: number;
  total: number;
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
const STATUS_CONFIG: Record<
  string,
  { step: number; label: string; isBlocking: boolean; color: string }
> = {
  // Step 1: Setup
  pending: { step: 1, label: "Setting up", isBlocking: false, color: "text-muted-foreground" },

  // Step 2: Audio Search (optional path)
  searching_audio: { step: 2, label: "Searching for audio", isBlocking: false, color: "text-blue-400" },
  awaiting_audio_selection: { step: 2, label: "Select audio source", isBlocking: true, color: "text-amber-400" },

  // Step 3: Download
  downloading_audio: { step: 3, label: "Downloading audio", isBlocking: false, color: "text-blue-400" },
  downloading: { step: 3, label: "Downloading", isBlocking: false, color: "text-blue-400" },

  // Step 3.5: Audio Editing (optional, BLOCKING)
  awaiting_audio_edit: { step: 3, label: "Edit audio", isBlocking: true, color: "text-amber-400" },
  in_audio_edit: { step: 3, label: "Editing audio", isBlocking: true, color: "text-blue-400" },
  audio_edit_complete: { step: 3, label: "Audio edited", isBlocking: false, color: "text-teal-400" },

  // Step 4: Parallel Processing (Audio + Lyrics)
  separating_stage1: { step: 4, label: "Separating audio (1/2)", isBlocking: false, color: "text-purple-400" },
  separating_stage2: { step: 4, label: "Separating audio (2/2)", isBlocking: false, color: "text-purple-400" },
  audio_complete: { step: 4, label: "Audio ready, processing lyrics", isBlocking: false, color: "text-purple-400" },
  transcribing: { step: 4, label: "Transcribing lyrics", isBlocking: false, color: "text-blue-400" },
  correcting: { step: 4, label: "Correcting lyrics", isBlocking: false, color: "text-blue-400" },
  lyrics_complete: { step: 4, label: "Lyrics ready, processing audio", isBlocking: false, color: "text-teal-400" },

  // Step 5: Screen Generation
  generating_screens: { step: 5, label: "Generating screens", isBlocking: false, color: "text-cyan-400" },
  applying_padding: { step: 5, label: "Syncing countdown", isBlocking: false, color: "text-cyan-400" },

  // Step 6: Review (BLOCKING - requires user action)
  awaiting_review: { step: 6, label: "Review lyrics", isBlocking: true, color: "text-amber-400" },
  in_review: { step: 6, label: "In review", isBlocking: true, color: "text-blue-400" },

  // Step 7: Video Rendering
  review_complete: { step: 7, label: "Starting render", isBlocking: false, color: "text-teal-400" },
  rendering_video: { step: 7, label: "Rendering video", isBlocking: false, color: "text-indigo-400" },

  // Step 8: Instrumental Selection (BLOCKING - requires user action)
  awaiting_instrumental_selection: { step: 8, label: "Select instrumental", isBlocking: true, color: "text-amber-400" },

  // Step 9: Final Encoding
  instrumental_selected: { step: 9, label: "Starting final encode", isBlocking: false, color: "text-pink-400" },
  generating_video: { step: 9, label: "Generating final video", isBlocking: false, color: "text-violet-400" },
  encoding: { step: 9, label: "Encoding video", isBlocking: false, color: "text-violet-400" },
  packaging: { step: 9, label: "Packaging files", isBlocking: false, color: "text-violet-400" },

  // Step 10: Distribution / Complete
  uploading: { step: 10, label: "Uploading", isBlocking: false, color: "text-green-400" },
  notifying: { step: 10, label: "Sending notifications", isBlocking: false, color: "text-green-400" },
  complete: { step: 10, label: "Complete", isBlocking: false, color: "text-green-400" },
  prep_complete: { step: 10, label: "Prep complete", isBlocking: false, color: "text-green-400" },

  // Terminal states (no step progression)
  failed: { step: 0, label: "Failed", isBlocking: false, color: "text-red-400" },
  cancelled: { step: 0, label: "Cancelled", isBlocking: false, color: "text-muted-foreground" },
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
      return "Processing complete";
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
      return "Audio 1/2";
    case "separating_stage2":
      return "Audio 2/2";
    case "audio_complete":
      return "Audio done";
    default:
      return "Audio";
  }
}

function getShortLyricsStatus(stage: string): string {
  switch (stage) {
    case "transcribing":
      return "Transcribing";
    case "correcting":
      return "Correcting";
    case "lyrics_complete":
      return "Lyrics done";
    default:
      return "Lyrics";
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

