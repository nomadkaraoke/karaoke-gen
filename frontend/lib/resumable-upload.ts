/**
 * Dependency-free client for GCS resumable upload sessions.
 *
 * Why: single-shot signed PUT URLs restart the whole file on any interruption.
 * A resumable session accepts sequential chunks with Content-Range headers and
 * remembers what it has persisted (in 256KiB increments), so after ANY failure
 * — network switch (4G→WiFi), laptop sleep, dropped connection, page reload —
 * we ask the session for its offset and continue from that exact byte. Session
 * URIs are capability tokens valid for ~1 week and are connection-independent.
 *
 * Resilience behaviours:
 * - Always queries the persisted offset before uploading (uniform fresh/resume
 *   path; a new session simply reports offset 0).
 * - Stall detection: a chunk with no progress events for `stallTimeoutMs` is
 *   aborted and retried — slow-but-moving connections are never killed.
 * - Exponential backoff with jitter between retries; the wait is cut short as
 *   soon as the browser fires `online` or the tab becomes visible again.
 * - While `navigator.onLine` is false we pause (state "waiting-online") instead
 *   of burning retries, and resume immediately on the `online` event.
 * - Permanent errors (4xx, e.g. 410 session expired) throw immediately so the
 *   caller can recreate the session; transient ones (network, 5xx, stalls,
 *   429/408) retry up to `maxConsecutiveFailures`.
 * - Progress reports persisted+in-flight bytes with an EWMA throughput and ETA.
 */

// GCS requires every chunk except the last to be a multiple of 256KiB.
export const CHUNK_ALIGNMENT = 256 * 1024
const DEFAULT_CHUNK_SIZE = 8 * 1024 * 1024
const MIN_CHUNK_SIZE = CHUNK_ALIGNMENT
const MAX_CHUNK_SIZE = 32 * 1024 * 1024
// Adaptive sizing: grow when chunks complete fast, shrink when they crawl.
const FAST_CHUNK_SECONDS = 5
const SLOW_CHUNK_SECONDS = 30

export interface ResumableProgress {
  /** Bytes persisted by GCS plus bytes of the in-flight chunk already sent. */
  loaded: number
  total: number
  /** EWMA upload throughput; null until the first measurement. */
  bytesPerSecond: number | null
  /** Estimated seconds remaining; null until throughput is known. */
  etaSeconds: number | null
  state: "uploading" | "waiting-online" | "retrying"
}

export interface ResumableUploadOptions {
  onProgress?: (p: ResumableProgress) => void
  signal?: AbortSignal
  /** Initial chunk size; adapted at runtime. Must be a 256KiB multiple. */
  chunkSize?: number
  /** Consecutive transient failures before giving up. Default 8. */
  maxConsecutiveFailures?: number
  /** Abort a chunk if no upload progress for this long. Default 45s. */
  stallTimeoutMs?: number
  /** Base for exponential backoff. Default 1000ms (tests pass ~0). */
  baseRetryDelayMs?: number
}

export class ResumableUploadError extends Error {
  status: number
  permanent: boolean
  constructor(message: string, status: number, permanent: boolean) {
    super(message)
    this.name = "ResumableUploadError"
    this.status = status
    this.permanent = permanent
  }
}

interface ChunkResult {
  status: number
  /** Next byte GCS expects, parsed from the 308 Range header when present. */
  confirmedOffset: number | null
  complete: boolean
}

function alignDown(n: number): number {
  return Math.max(MIN_CHUNK_SIZE, Math.floor(n / CHUNK_ALIGNMENT) * CHUNK_ALIGNMENT)
}

function parseRangeEnd(rangeHeader: string | null): number | null {
  // "bytes=0-1048575" → next offset 1048576
  const m = rangeHeader?.match(/bytes=\d+-(\d+)/)
  return m ? parseInt(m[1], 10) + 1 : null
}

function isPermanentStatus(status: number): boolean {
  // 4xx are permanent except the retryable pair; 0 (network) and 5xx are transient.
  return status >= 400 && status < 500 && status !== 408 && status !== 429
}

function throwIfAborted(signal?: AbortSignal): void {
  if (signal?.aborted) throw new DOMException("Upload aborted", "AbortError")
}

/**
 * Ask the session how much it has persisted.
 * Returns the next offset to send from, or "complete" if the object finished.
 */
export function queryUploadOffset(
  sessionUri: string,
  total: number,
  signal?: AbortSignal,
): Promise<number | "complete"> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open("PUT", sessionUri, true)
    xhr.setRequestHeader("Content-Range", `bytes */${total}`)
    const onAbort = () => xhr.abort()
    signal?.addEventListener("abort", onAbort, { once: true })
    xhr.onload = () => {
      signal?.removeEventListener("abort", onAbort)
      if (xhr.status === 200 || xhr.status === 201) return resolve("complete")
      if (xhr.status === 308) {
        // No Range header on a fresh session → nothing persisted yet.
        return resolve(parseRangeEnd(xhr.getResponseHeader("Range")) ?? 0)
      }
      reject(new ResumableUploadError(`Offset query failed: ${xhr.status}`, xhr.status, isPermanentStatus(xhr.status)))
    }
    xhr.onerror = () => {
      signal?.removeEventListener("abort", onAbort)
      reject(new ResumableUploadError("Offset query failed: network error", 0, false))
    }
    xhr.onabort = () => reject(new DOMException("Upload aborted", "AbortError"))
    xhr.send()
  })
}

function sendChunk(
  sessionUri: string,
  file: File,
  offset: number,
  end: number,
  stallTimeoutMs: number,
  onChunkProgress: (bytesSent: number) => void,
  signal?: AbortSignal,
): Promise<ChunkResult> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open("PUT", sessionUri, true)
    xhr.setRequestHeader("Content-Range", `bytes ${offset}-${end - 1}/${file.size}`)

    let stallTimer: ReturnType<typeof setTimeout> | null = null
    let stalled = false
    const resetStall = () => {
      if (stallTimer) clearTimeout(stallTimer)
      stallTimer = setTimeout(() => {
        stalled = true
        xhr.abort()
      }, stallTimeoutMs)
    }
    const cleanup = () => {
      if (stallTimer) clearTimeout(stallTimer)
      signal?.removeEventListener("abort", onAbort)
    }
    const onAbort = () => xhr.abort()
    signal?.addEventListener("abort", onAbort, { once: true })

    xhr.upload.onprogress = (e) => {
      resetStall()
      if (e.lengthComputable) onChunkProgress(e.loaded)
    }
    xhr.onload = () => {
      cleanup()
      if (xhr.status === 200 || xhr.status === 201) {
        return resolve({ status: xhr.status, confirmedOffset: null, complete: true })
      }
      if (xhr.status === 308) {
        return resolve({
          status: 308,
          confirmedOffset: parseRangeEnd(xhr.getResponseHeader("Range")),
          complete: false,
        })
      }
      reject(new ResumableUploadError(`Chunk upload failed: ${xhr.status}`, xhr.status, isPermanentStatus(xhr.status)))
    }
    xhr.onerror = () => {
      cleanup()
      reject(new ResumableUploadError("Chunk upload failed: network error", 0, false))
    }
    xhr.onabort = () => {
      cleanup()
      if (signal?.aborted) return reject(new DOMException("Upload aborted", "AbortError"))
      // Stall-triggered abort → transient failure, the retry loop takes over.
      reject(new ResumableUploadError(stalled ? "Chunk stalled" : "Chunk aborted", 0, false))
    }
    resetStall()
    // file.slice() yields a Blob with an empty type, so XHR sends no
    // Content-Type header — the session's content type (fixed at creation)
    // applies, and we avoid a mismatched-header rejection.
    xhr.send(file.slice(offset, end))
  })
}

/** Resolves when back online / tab visible / after the backoff delay — whichever first. */
function interruptibleDelay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    let done = false
    const finish = () => {
      if (done) return
      done = true
      clearTimeout(timer)
      window.removeEventListener("online", finish)
      document.removeEventListener("visibilitychange", onVisible)
      signal?.removeEventListener("abort", finish)
      resolve()
    }
    const onVisible = () => {
      if (document.visibilityState === "visible") finish()
    }
    const timer = setTimeout(finish, ms)
    window.addEventListener("online", finish, { once: true })
    document.addEventListener("visibilitychange", onVisible)
    signal?.addEventListener("abort", finish, { once: true })
  })
}

/** Resolves once the browser reports being online (immediately if it already is). */
function waitForOnline(signal?: AbortSignal): Promise<void> {
  if (typeof navigator === "undefined" || navigator.onLine) return Promise.resolve()
  return new Promise((resolve) => {
    const finish = () => {
      window.removeEventListener("online", finish)
      signal?.removeEventListener("abort", finish)
      resolve()
    }
    window.addEventListener("online", finish, { once: true })
    signal?.addEventListener("abort", finish, { once: true })
  })
}

/**
 * Upload a file to a GCS resumable session URI, resuming as needed.
 * Resolves when GCS confirms the complete object; rejects with
 * ResumableUploadError (permanent=true means recreate the session) or
 * an AbortError DOMException.
 */
export async function uploadResumable(
  sessionUri: string,
  file: File,
  options: ResumableUploadOptions = {},
): Promise<void> {
  const {
    onProgress,
    signal,
    chunkSize: initialChunkSize = DEFAULT_CHUNK_SIZE,
    maxConsecutiveFailures = 8,
    stallTimeoutMs = 45_000,
    baseRetryDelayMs = 1_000,
  } = options

  let chunkSize = alignDown(initialChunkSize)
  let consecutiveFailures = 0
  // EWMA throughput across progress samples.
  let rate: number | null = null
  let lastSampleTime = 0
  let lastSampleLoaded = 0

  const report = (loaded: number, state: ResumableProgress["state"]) => {
    const etaSeconds = rate && rate > 0 ? Math.max(0, (file.size - loaded) / rate) : null
    onProgress?.({ loaded, total: file.size, bytesPerSecond: rate, etaSeconds, state })
  }
  const sampleRate = (loaded: number) => {
    const now = Date.now()
    if (lastSampleTime > 0) {
      const dt = (now - lastSampleTime) / 1000
      const dBytes = loaded - lastSampleLoaded
      if (dt > 0.2 && dBytes >= 0) {
        const instant = dBytes / dt
        rate = rate === null ? instant : 0.2 * instant + 0.8 * rate
        lastSampleTime = now
        lastSampleLoaded = loaded
      }
    } else {
      lastSampleTime = now
      lastSampleLoaded = loaded
    }
  }

  // Uniform start: ask the session where to begin (0 for a fresh session).
  throwIfAborted(signal)
  let offset = await (async () => {
    const r = await queryUploadOffset(sessionUri, file.size, signal)
    return r === "complete" ? file.size : r
  })()
  if (offset >= file.size) {
    // Already fully persisted (e.g. resume after the final chunk landed).
    report(file.size, "uploading")
    return
  }

  while (true) {
    throwIfAborted(signal)

    // Don't attempt (or burn retries) while known-offline; resume on `online`.
    if (typeof navigator !== "undefined" && !navigator.onLine) {
      report(offset, "waiting-online")
      await waitForOnline(signal)
      throwIfAborted(signal)
    }

    const end = Math.min(offset + chunkSize, file.size)
    const chunkStart = Date.now()
    try {
      const result = await sendChunk(
        sessionUri, file, offset, end, stallTimeoutMs,
        (sent) => {
          const loaded = offset + sent
          sampleRate(loaded)
          report(loaded, "uploading")
        },
        signal,
      )
      consecutiveFailures = 0

      if (result.complete) {
        report(file.size, "uploading")
        return
      }
      // Trust the Range header when readable; otherwise our own accounting
      // (a 308 acknowledges the range we just sent).
      offset = result.confirmedOffset ?? end

      // Adapt chunk size to the observed connection speed.
      const secs = (Date.now() - chunkStart) / 1000
      if (secs < FAST_CHUNK_SECONDS && chunkSize < MAX_CHUNK_SIZE) {
        chunkSize = alignDown(Math.min(chunkSize * 2, MAX_CHUNK_SIZE))
      } else if (secs > SLOW_CHUNK_SECONDS && chunkSize > MIN_CHUNK_SIZE) {
        chunkSize = alignDown(Math.max(chunkSize / 2, MIN_CHUNK_SIZE))
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") throw err
      if (err instanceof ResumableUploadError && err.permanent) throw err

      consecutiveFailures++
      if (consecutiveFailures > maxConsecutiveFailures) {
        throw err instanceof ResumableUploadError
          ? err
          : new ResumableUploadError(String(err), 0, false)
      }

      // Backoff with jitter, cut short by online/visibility events.
      report(offset, "retrying")
      const delay = Math.min(30_000, baseRetryDelayMs * 2 ** (consecutiveFailures - 1)) * (0.5 + Math.random())
      await interruptibleDelay(delay, signal)
      throwIfAborted(signal)

      // Recover the true persisted offset so no persisted byte is re-sent.
      try {
        const r = await queryUploadOffset(sessionUri, file.size, signal)
        if (r === "complete") {
          report(file.size, "uploading")
          return
        }
        offset = r
      } catch (queryErr) {
        if (queryErr instanceof DOMException && queryErr.name === "AbortError") throw queryErr
        if (queryErr instanceof ResumableUploadError && queryErr.permanent) throw queryErr
        // Transient query failure: keep the last known offset; a 308 for a
        // range GCS already has is accepted, so worst case we resend one chunk.
      }
    }
  }
}
