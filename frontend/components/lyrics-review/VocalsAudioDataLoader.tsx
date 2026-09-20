import { AudioData, AudioNotReadyError, fetchAudioData, fetchVocalsPeaks, isTransientAudioError } from '@/lib/audio-data'
import { reportDegradationEvent } from '@/lib/degradation-events'
import { createContext, PropsWithChildren, useEffect, useState } from 'react'

// A first paint whose waveform strips took longer than this to appear counts as
// a degraded experience worth recording (Andrew's "wait 1+ minutes" complaint).
// Excludes legitimate separation-in-progress waits shorter than one 202 poll.
const SLOW_LOAD_REPORT_MS = 20_000

export const VocalsAudioDataLoaderContext = createContext<{ audioData: AudioData | null }>({ audioData: null })

export interface AudioDataLoaderProps extends PropsWithChildren {
	audioUrl: string | null
	/** Pre-computed peak-envelope endpoint (~150 KB JSON). Preferred over
	 *  downloading + decoding the whole stem from audioUrl; when it fails
	 *  terminally the loader falls back to the full audio decode. */
	peaksUrl?: string | null
}

// Audio separation runs in the background while the user reviews lyrics, so the
// vocal stem often doesn't exist yet on first page load (endpoint returns 202).
// Poll until it appears; separation takes a few minutes at most, so cap the
// polling rather than retrying forever if something upstream is wedged.
const NOT_READY_RETRY_MS = 15_000
const NOT_READY_MAX_RETRIES = 40 // 40 × 15s = 10 minutes

// Transient failures (a 500/timeout while the backend is briefly overloaded —
// e.g. many review tabs opened at once) previously gave up silently, leaving the
// Waveforms view stripless until a manual reload. Retry a few times with backoff.
const TRANSIENT_RETRY_DELAYS_MS = [5_000, 15_000, 30_000, 60_000]

export const VocalsAudioDataLoader = ({ audioUrl, peaksUrl, children }: AudioDataLoaderProps) => {
	const [audioData, setAudioData] = useState<AudioData | null>(null)

	useEffect(() => {
		if (!audioUrl && !peaksUrl) return

		// Guard against (a) an unhandled rejection when the endpoint 404s (no vocal
		// stem for this job) and (b) a stale in-flight fetch resolving after a newer
		// audioUrl has already been set.
		let cancelled = false
		let retryTimer: ReturnType<typeof setTimeout> | undefined
		const startedAt = Date.now()

		const load = (attempt: number, transientAttempt: number, usePeaks: boolean) => {
			const source =
				usePeaks && peaksUrl ? fetchVocalsPeaks(peaksUrl) : fetchAudioData(audioUrl!)
			source
				.then((audioData) => {
					if (cancelled) return
					const elapsedMs = Date.now() - startedAt
					if (elapsedMs >= SLOW_LOAD_REPORT_MS) {
						reportDegradationEvent('waveform_slow', {
							elapsed_ms: elapsedMs,
							not_ready_polls: attempt,
							transient_retries: transientAttempt,
							used_peaks: usePeaks,
						})
					}
					setAudioData(audioData)
				})
				.catch((error) => {
					if (cancelled) return
					if (error instanceof AudioNotReadyError && attempt < NOT_READY_MAX_RETRIES) {
						retryTimer = setTimeout(() => load(attempt + 1, transientAttempt, usePeaks), NOT_READY_RETRY_MS)
						return
					}
					// Peaks get ONE quick retry, then we fall back to the audio path
					// (which has the full backoff ladder) — a flaky peaks endpoint
					// must not delay strips by the whole retry ladder first.
					const maxTransientRetries = usePeaks ? 1 : TRANSIENT_RETRY_DELAYS_MS.length
					if (isTransientAudioError(error) && transientAttempt < maxTransientRetries) {
						const delay = TRANSIENT_RETRY_DELAYS_MS[transientAttempt]
						console.warn(`Vocals ${usePeaks ? 'peaks' : 'audio'} load failed, retrying in ${delay / 1000}s`, error)
						retryTimer = setTimeout(() => load(attempt, transientAttempt + 1, usePeaks), delay)
						return
					}
					if (usePeaks && audioUrl) {
						// Peaks endpoint terminally failed (or exhausted retries) —
						// fall back to the original full-download-and-decode path so a
						// peaks-side problem never costs the user their waveforms.
						console.warn('Vocals peaks unavailable, falling back to full audio decode', error)
						load(attempt, 0, false)
						return
					}
					console.error('Failed to load vocals audio data', error)
					reportDegradationEvent('waveform_failed', {
						message: error instanceof Error ? error.message : String(error),
						elapsed_ms: Date.now() - startedAt,
						not_ready_polls: attempt,
						transient_retries: transientAttempt,
					})
					setAudioData(null)
				})
		}

		load(0, 0, Boolean(peaksUrl))

		return () => {
			cancelled = true
			if (retryTimer) clearTimeout(retryTimer)
			setAudioData(null)
		}
	}, [audioUrl, peaksUrl])

	return (
		<VocalsAudioDataLoaderContext.Provider value={{ audioData }}>
			{children}
		</VocalsAudioDataLoaderContext.Provider>
	)
}
