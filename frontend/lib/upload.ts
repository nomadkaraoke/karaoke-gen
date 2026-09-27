/**
 * Shared browser → GCS upload primitives. Every flow that sends a user's file
 * (job creation, tenant jobs, instrumental review) goes through these so progress
 * reporting, error shapes and the progress modal behave identically everywhere.
 *
 * Files never go through our API: Cloud Run caps request bodies at 32 MiB, so the
 * backend hands out a signed PUT URL and the browser uploads straight to GCS.
 */
import { ApiError } from './api-error';

export interface UploadProgress {
  phase: 'creating' | 'uploading' | 'finalizing';
  loaded: number;
  total: number;
  /** Set when several files upload in sequence (e.g. mix + instrumental). */
  fileName?: string;
  fileIndex?: number;
  fileCount?: number;
}

export interface SignedUploadTarget {
  file: File;
  url: string;
  contentType: string;
}

/**
 * The backend requires mix and instrumental within 0.5s (exact decode). Browser
 * <audio> metadata can be a second or two off (e.g. VBR MP3 without a Xing
 * header), so the client only blocks clear mismatches and leaves the precise
 * check to the server.
 */
export const CLIENT_DURATION_TOLERANCE_SECONDS = 1.5;

/** Keep in sync with MAX_INSTRUMENTAL_UPLOAD_BYTES in backend/api/routes/jobs.py. */
export const MAX_INSTRUMENTAL_UPLOAD_BYTES = 200 * 1024 * 1024;

export type InstrumentalCheck =
  | { ok: true }
  | { ok: false; reason: 'tooLarge'; sizeMb: number; maxMb: number }
  | { ok: false; reason: 'mismatch'; fileSeconds: number; expectedSeconds: number };

/**
 * Pre-upload sanity check for a user-supplied instrumental, shared by every
 * flow that accepts one. `expected` is the song's length (seconds) or the mix
 * file itself. Unknown durations pass — the backend re-checks.
 */
export async function checkInstrumentalFile(
  file: File,
  expected: number | File | null,
): Promise<InstrumentalCheck> {
  if (file.size > MAX_INSTRUMENTAL_UPLOAD_BYTES) {
    return {
      ok: false,
      reason: 'tooLarge',
      sizeMb: Math.round(file.size / (1024 * 1024)),
      maxMb: MAX_INSTRUMENTAL_UPLOAD_BYTES / (1024 * 1024),
    };
  }
  const [fileSeconds, expectedSeconds] = await Promise.all([
    getAudioFileDuration(file),
    expected instanceof File ? getAudioFileDuration(expected) : Promise.resolve(expected && expected > 0 ? expected : null),
  ]);
  if (durationsMismatch(fileSeconds, expectedSeconds)) {
    return { ok: false, reason: 'mismatch', fileSeconds: fileSeconds as number, expectedSeconds: expectedSeconds as number };
  }
  return { ok: true };
}

/**
 * PUT one file to a signed URL with upload progress.
 * Rejects with ApiError (status 0 = network failure / connection dropped).
 */
export function putFileToSignedUrl(
  url: string,
  file: File,
  contentType: string,
  onProgress?: (loaded: number, total: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', url, true);
    xhr.setRequestHeader('Content-Type', contentType);

    if (onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress(e.loaded, e.total);
      };
    }

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve();
      } else {
        reject(new ApiError(`Upload failed: ${xhr.status} ${xhr.statusText}`.trim(), xhr.status));
      }
    };
    xhr.onerror = () => reject(new ApiError('Upload failed: network error', 0));
    xhr.onabort = () => reject(new ApiError('Upload cancelled', 0));
    xhr.send(file);
  });
}

/**
 * Upload several files in sequence, reporting aggregate progress across all of
 * them (so one progress bar covers e.g. mix + instrumental).
 */
export async function uploadFilesToSignedUrls(
  targets: SignedUploadTarget[],
  onProgress?: (progress: UploadProgress) => void,
): Promise<void> {
  const total = targets.reduce((sum, t) => sum + t.file.size, 0);
  let done = 0;
  for (let i = 0; i < targets.length; i++) {
    const { file, url, contentType } = targets[i];
    const report = (loaded: number) =>
      onProgress?.({
        phase: 'uploading',
        loaded: done + loaded,
        total,
        fileName: file.name,
        fileIndex: i + 1,
        fileCount: targets.length,
      });
    report(0);
    await putFileToSignedUrl(url, file, contentType, (loaded) => report(loaded));
    done += file.size;
  }
}

/** Duration of a local audio file in seconds via <audio> metadata, or null if the browser can't tell. */
export function getAudioFileDuration(file: File, timeoutMs = 10000): Promise<number | null> {
  if (typeof window === 'undefined' || typeof URL.createObjectURL !== 'function') {
    return Promise.resolve(null);
  }
  return new Promise((resolve) => {
    const audio = document.createElement('audio');
    const src = URL.createObjectURL(file);
    let settled = false;
    const finish = (value: number | null) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      URL.revokeObjectURL(src);
      audio.removeAttribute('src');
      resolve(value);
    };
    const timer = setTimeout(() => finish(null), timeoutMs);
    audio.preload = 'metadata';
    audio.onloadedmetadata = () => finish(Number.isFinite(audio.duration) ? audio.duration : null);
    audio.onerror = () => finish(null);
    audio.src = src;
  });
}

/** True when both durations are known and clearly differ (see CLIENT_DURATION_TOLERANCE_SECONDS). */
export function durationsMismatch(a: number | null, b: number | null): boolean {
  if (a == null || b == null) return false;
  return Math.abs(a - b) > CLIENT_DURATION_TOLERANCE_SECONDS;
}

/** True for network-level upload failures (offline, connection dropped, tab suspended). */
export function isNetworkUploadError(err: unknown): boolean {
  if (err instanceof ApiError) return err.status === 0;
  return err instanceof TypeError || /network|failed to fetch|load failed/i.test(String((err as Error)?.message ?? ''));
}
