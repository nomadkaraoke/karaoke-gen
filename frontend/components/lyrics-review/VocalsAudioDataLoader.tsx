import { AudioData, fetchAudioData } from '@/lib/audio-data'
import { createContext, PropsWithChildren, useEffect, useState } from 'react'

export const VocalsAudioDataLoaderContext = createContext<{ audioData: AudioData | null }>({ audioData: null })

export interface AudioDataLoaderProps extends PropsWithChildren {
	audioUrl: string | null
}

export const VocalsAudioDataLoader = ({ audioUrl, children }: AudioDataLoaderProps) => {
	const [audioData, setAudioData] = useState<AudioData | null>(null)

	useEffect(() => {
		if (!audioUrl) return

		// Guard against (a) an unhandled rejection when the endpoint 404s (no vocal
		// stem for this job) and (b) a stale in-flight fetch resolving after a newer
		// audioUrl has already been set.
		let cancelled = false

		fetchAudioData(audioUrl)
			.then((audioData) => {
				if (!cancelled) setAudioData(audioData)
			})
			.catch((error) => {
				if (!cancelled) {
					console.error('Failed to load vocals audio data', error)
					setAudioData(null)
				}
			})

		return () => {
			cancelled = true
			setAudioData(null)
		}
	}, [audioUrl])

	return (
		<VocalsAudioDataLoaderContext.Provider value={{ audioData }}>
			{children}
		</VocalsAudioDataLoaderContext.Provider>
	)
}
