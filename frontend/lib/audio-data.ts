export interface AudioData {
	duration: number
	// Downsampled absolute-amplitude envelope: one peak per bucket. Storing the
	// envelope instead of the raw PCM keeps a whole vocal track in ~hundreds of KB
	// (peaksPerSecond * duration * 4 bytes) rather than the tens of MB a full
	// Float32Array of decoded samples would occupy, which noticeably slows the app.
	peaks: Float32Array
	peaksPerSecond: number
}

// Resolution of the precomputed peak envelope. High enough to stay crisp when a
// short segment is zoomed across the full timeline width, low enough to keep the
// whole track tiny in memory. 400/s over a 4-minute song ≈ 96k floats (~384 KB).
const PEAKS_PER_SECOND = 400

export async function fetchAudioData(url: string): Promise<AudioData> {
	const arrayBuffer = await fetch(url).then((response) => response.arrayBuffer())

	const context = new AudioContext()

	try {
		const decoded = await context.decodeAudioData(arrayBuffer)

		const { duration, numberOfChannels, length } = decoded
		const bucketCount = Math.max(1, Math.ceil(duration * PEAKS_PER_SECOND))
		const samplesPerBucket = Math.max(1, Math.floor(length / bucketCount))
		const peaks = new Float32Array(bucketCount)

		// Collapse all channels into a single envelope by taking the maximum
		// absolute sample value in each bucket. The raw decoded buffer is released
		// when this function returns; only the compact `peaks` array is retained.
		for (let channelIdx = 0; channelIdx < numberOfChannels; channelIdx++) {
			const channelData = decoded.getChannelData(channelIdx)
			for (let i = 0; i < length; i++) {
				const bucket = Math.min(bucketCount - 1, Math.floor(i / samplesPerBucket))
				const amplitude = Math.abs(channelData[i])
				if (amplitude > peaks[bucket]) {
					peaks[bucket] = amplitude
				}
			}
		}

		return {
			duration,
			peaks,
			peaksPerSecond: PEAKS_PER_SECOND
		}
	} finally {
		context.close()
	}
}
