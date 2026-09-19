import { AudioData, AudioNotReadyError, fetchAudioData, isTransientAudioError } from '@/lib/audio-data'
import { reportDegradationEvent } from '@/lib/degradation-events'
import { createContext, PropsWithChildren, useEffect, useState } from 'react'

// A first paint whose waveform strips took longer than this to appear counts as
// a degraded experience worth recording (Andrew's "wait 1+ minutes" complaint).
// Excludes legitimate separation-in-progress waits shorter than one 202 poll.
const SLOW_LOAD_REPORT_MS = 20_000

export const VocalsAudioDataLoaderContext = createContext<{ audioData: AudioData | null }>({ audioData: null })

export interface AudioDataLoaderProps extends PropsWithChildren {
	audioUrl: string | null
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

export const VocalsAudioDataLoader = ({ audioUrl, children }: AudioDataLoaderProps) => {
	const [audioData, setAudioData] = useState<AudioData | null>(null)

	useEffect(() => {
		if (!audioUrl) return

		// Guard against (a) an unhandled rejection when the endpoint 404s (no vocal
		// stem for this job) and (b) a stale in-flight fetch resolving after a newer
		// audioUrl has already been set.
		let cancelled = false
		let retryTimer: ReturnType<typeof setTimeout> | undefined
		const startedAt = Date.now()

		const load = (attempt: number, transientAttempt: number) => {
			fetchAudioData(audioUrl)
				.then((audioData) => {
					if (cancelled) return
					const elapsedMs = Date.now() - startedAt
					if (elapsedMs >= SLOW_LOAD_REPORT_MS) {
						reportDegradationEvent('waveform_slow', {
							elapsed_ms: elapsedMs,
							not_ready_polls: attempt,
							transient_retries: transientAttempt,
						})
					}
					setAudioData(audioData)
				})
				.catch((error) => {
					if (cancelled) return
					if (error instanceof AudioNotReadyError && attempt < NOT_READY_MAX_RETRIES) {
						retryTimer = setTimeout(() => load(attempt + 1, transientAttempt), NOT_READY_RETRY_MS)
						return
					}
					if (isTransientAudioError(error) && transientAttempt < TRANSIENT_RETRY_DELAYS_MS.length) {
						const delay = TRANSIENT_RETRY_DELAYS_MS[transientAttempt]
						console.warn(`Vocals audio load failed, retrying in ${delay / 1000}s`, error)
						retryTimer = setTimeout(() => load(attempt, transientAttempt + 1), delay)
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

		load(0, 0)

		return () => {
			cancelled = true
			if (retryTimer) clearTimeout(retryTimer)
			setAudioData(null)
		}
	}, [audioUrl])

	return (
		<VocalsAudioDataLoaderContext.Provider value={{ audioData }}>
			{children}
		</VocalsAudioDataLoaderContext.Provider>
	)
}
