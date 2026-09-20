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

// The vocals endpoint returns 202 while audio separation is still running (the
// user can open lyrics review before the vocal stem has been uploaded). Callers
// should catch this and retry after a delay rather than giving up.
export class AudioNotReadyError extends Error {
	constructor() {
		super('Vocals audio not ready yet (separation in progress)')
		this.name = 'AudioNotReadyError'
	}
}

// Non-2xx response from the vocals endpoint. Carries the status so callers can
// tell transient overload (5xx/429 — worth retrying) from terminal answers
// (404: this job has no vocal stem; retrying would never succeed).
export class AudioFetchError extends Error {
	constructor(public readonly status: number) {
		super(`Failed to fetch vocals audio: ${status}`)
		this.name = 'AudioFetchError'
	}
}

export function isTransientAudioError(error: unknown): boolean {
	if (error instanceof AudioFetchError) {
		return error.status >= 500 || error.status === 429
	}
	// fetch() rejects with a TypeError on network-level failures (offline,
	// connection reset, CORS blip) — all worth retrying.
	return error instanceof TypeError
}

// Minimal structural subset of AudioBuffer, so the peak math is testable
// without constructing a real (browser-only) AudioContext.
export interface DecodedAudioLike {
	duration: number
	length: number
	numberOfChannels: number
	getChannelData(channel: number): Float32Array
}

export function computePeaks(decoded: DecodedAudioLike): AudioData {
	const { duration, numberOfChannels, length } = decoded
	const bucketCount = Math.max(1, Math.ceil(duration * PEAKS_PER_SECOND))
	// Fractional on purpose: at a 44.1 kHz AudioContext this is 110.25. Flooring
	// it to an integer stride made the envelope's clock run ~0.23% fast, so the
	// waveform drifted visibly late as the song progressed (~0.4s by the 3-minute
	// mark). With floored per-bucket boundaries the remaining error is bounded by
	// the ceil() rounding of bucketCount: under one bucket (2.5 ms) anywhere in
	// the track, non-accumulating and sub-pixel at any timeline zoom.
	const samplesPerBucket = length / bucketCount
	const peaks = new Float32Array(bucketCount)

	// Collapse all channels into a single envelope by taking the maximum
	// absolute sample value in each bucket. Iterate per bucket (not per sample)
	// so we avoid a division + Math.floor on every one of the ~millions of
	// samples, which would otherwise freeze the main thread while loading.
	// The raw decoded buffer is released when this function returns; only the
	// compact `peaks` array is retained.
	for (let channelIdx = 0; channelIdx < numberOfChannels; channelIdx++) {
		const channelData = decoded.getChannelData(channelIdx)
		for (let bucket = 0; bucket < bucketCount; bucket++) {
			const start = Math.floor(bucket * samplesPerBucket)
			if (start >= length) break
			const end = bucket === bucketCount - 1 ? length : Math.min(length, Math.floor((bucket + 1) * samplesPerBucket))
			let peak = peaks[bucket]
			for (let i = start; i < end; i++) {
				const amplitude = Math.abs(channelData[i])
				if (amplitude > peak) {
					peak = amplitude
				}
			}
			peaks[bucket] = peak
		}
	}

	return {
		duration,
		peaks,
		peaksPerSecond: PEAKS_PER_SECOND
	}
}

/**
 * Fetch the server-side pre-computed vocals peak envelope (~150 KB JSON) and
 * adapt it to the same AudioData shape computePeaks produces — so waveform
 * strips paint sub-second instead of after a multi-MB download + decode.
 * Throws AudioNotReadyError on 202 (separation still running) and
 * AudioFetchError on other non-2xx, mirroring fetchAudioData.
 */
export async function fetchVocalsPeaks(url: string): Promise<AudioData> {
	const response = await fetch(url)
	if (response.status === 202) {
		throw new AudioNotReadyError()
	}
	if (!response.ok) {
		throw new AudioFetchError(response.status)
	}
	const data = await response.json()
	if (data?.encoding !== 'u8' || typeof data.peaks_b64 !== 'string' || !data.peaks_b64) {
		throw new AudioFetchError(500) // unexpected payload — treat as transient server trouble
	}
	const raw = atob(data.peaks_b64)
	const peaks = new Float32Array(raw.length)
	for (let i = 0; i < raw.length; i++) {
		peaks[i] = raw.charCodeAt(i) / 255
	}
	return {
		duration: Number(data.duration_seconds) || 0,
		peaks,
		peaksPerSecond: Number(data.peaks_per_second) || PEAKS_PER_SECOND,
	}
}

export async function fetchAudioData(url: string): Promise<AudioData> {
	// fetch() resolves even for 4xx/5xx; the vocals endpoint 404s when a job has
	// no vocal stem. Fail explicitly so the caller sees "no vocals" rather than an
	// opaque decodeAudioData error on the JSON/HTML error body.
	const response = await fetch(url)
	if (response.status === 202) {
		throw new AudioNotReadyError()
	}
	if (!response.ok) {
		throw new AudioFetchError(response.status)
	}
	const arrayBuffer = await response.arrayBuffer()

	const context = new AudioContext()

	try {
		const decoded = await context.decodeAudioData(arrayBuffer)
		return computePeaks(decoded)
	} finally {
		context.close()
	}
}
