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

		fetchAudioData(audioUrl).then((audioData) => {
			setAudioData(audioData)
		})

		return () => {
			setAudioData(null)
		}
	}, [audioUrl])

	return (
		<VocalsAudioDataLoaderContext.Provider value={{ audioData }}>
			{children}
		</VocalsAudioDataLoaderContext.Provider>
	)
}
