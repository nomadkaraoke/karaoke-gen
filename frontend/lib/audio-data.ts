export interface AudioData {
	duration: number
	buffer: Float32Array
}

export async function fetchAudioData(url: string): Promise<AudioData> {
	const arrayBuffer = await fetch(url).then((response) => response.arrayBuffer())

	const context = new AudioContext()

	try {
		const decodedAudioBuffer = await context.decodeAudioData(arrayBuffer)

		const channelZeroData = decodedAudioBuffer.getChannelData(0)
		const combinedAudioBuffer: Float32Array = new Float32Array(channelZeroData)

		for (let channelIdx = 1; channelIdx < decodedAudioBuffer.numberOfChannels; channelIdx++) {
			const channelData = decodedAudioBuffer.getChannelData(channelIdx)
			for (let i = 0; i < combinedAudioBuffer.length; i++) {
				if (channelData[i] > combinedAudioBuffer[i]) {
					combinedAudioBuffer[i] = channelData[i]
				}
			}
		}

		return {
			duration: decodedAudioBuffer.duration,
			buffer: combinedAudioBuffer
		}
	} finally {
		context.close()
	}
}

