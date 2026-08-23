import { AudioData, AudioNotReadyError, fetchAudioData } from '@/lib/audio-data'
import { createContext, PropsWithChildren, useEffect, useState } from 'react'

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

export const VocalsAudioDataLoader = ({ audioUrl, children }: AudioDataLoaderProps) => {
	const [audioData, setAudioData] = useState<AudioData | null>(null)

	useEffect(() => {
		if (!audioUrl) return

		// Guard against (a) an unhandled rejection when the endpoint 404s (no vocal
		// stem for this job) and (b) a stale in-flight fetch resolving after a newer
		// audioUrl has already been set.
		let cancelled = false
		let retryTimer: ReturnType<typeof setTimeout> | undefined

		const load = (attempt: number) => {
			fetchAudioData(audioUrl)
				.then((audioData) => {
					if (!cancelled) setAudioData(audioData)
				})
				.catch((error) => {
					if (cancelled) return
					if (error instanceof AudioNotReadyError && attempt < NOT_READY_MAX_RETRIES) {
						retryTimer = setTimeout(() => load(attempt + 1), NOT_READY_RETRY_MS)
						return
					}
					console.error('Failed to load vocals audio data', error)
					setAudioData(null)
				})
		}

		load(0)

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
