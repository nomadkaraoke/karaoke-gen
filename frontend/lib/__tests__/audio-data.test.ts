import { computePeaks, DecodedAudioLike } from '@/lib/audio-data'

// computePeaks always emits 400 buckets/second (audio-data.ts PEAKS_PER_SECOND);
// mirrored here so the tests fail loudly if the resolution ever changes.
const PEAKS_PER_SECOND = 400

function makeBuffer(sampleRate: number, durationSeconds: number, numberOfChannels = 1): {
	decoded: DecodedAudioLike
	channels: Float32Array[]
} {
	const length = Math.round(sampleRate * durationSeconds)
	const channels = Array.from({ length: numberOfChannels }, () => new Float32Array(length))
	return {
		decoded: {
			duration: length / sampleRate,
			length,
			numberOfChannels,
			getChannelData: (channel: number) => channels[channel],
		},
		channels,
	}
}

describe('computePeaks', () => {
	it('reports peaksPerSecond and one bucket per 1/400s of audio', () => {
		const { decoded } = makeBuffer(48000, 10)
		const audioData = computePeaks(decoded)

		expect(audioData.peaksPerSecond).toBe(PEAKS_PER_SECOND)
		expect(audioData.duration).toBeCloseTo(10)
		expect(audioData.peaks.length).toBe(10 * PEAKS_PER_SECOND)
	})

	it('places an impulse in the bucket matching its real time at 48kHz (exact division)', () => {
		const { decoded, channels } = makeBuffer(48000, 10)
		const impulseTime = 7.3
		channels[0][Math.round(impulseTime * 48000)] = 0.9

		const { peaks } = computePeaks(decoded)

		const expectedBucket = Math.floor(impulseTime * PEAKS_PER_SECOND)
		expect(peaks[expectedBucket]).toBeCloseTo(0.9)
		expect(peaks.reduce((sum, p) => sum + (p > 0 ? 1 : 0), 0)).toBe(1)
	})

	// Regression: 44100/400 = 110.25 samples per bucket. Flooring that to an
	// integer stride made every bucket cover slightly too little real time, so
	// the envelope drifted late by ~2.3ms per second of audio — a visible
	// ~0.4s waveform offset by 3 minutes into a song on 44.1kHz output devices.
	it('does not drift at 44.1kHz (non-integer samples per bucket)', () => {
		const durationSeconds = 30
		const { decoded, channels } = makeBuffer(44100, durationSeconds)
		// Impulse near the end, where the old floored-stride drift was ~27 buckets.
		const impulseTime = 29.5
		channels[0][Math.round(impulseTime * 44100)] = 1.0

		const { peaks } = computePeaks(decoded)

		const hotBuckets = [] as number[]
		peaks.forEach((peak, bucket) => {
			if (peak > 0) hotBuckets.push(bucket)
		})
		expect(hotBuckets).toHaveLength(1)

		const expectedBucket = Math.floor(impulseTime * PEAKS_PER_SECOND)
		// Allow one bucket of slack (2.5ms) for boundary rounding; the pre-fix
		// code was 27 buckets (67ms) late here and fails this assertion.
		expect(Math.abs(hotBuckets[0] - expectedBucket)).toBeLessThanOrEqual(1)
	})

	it('keeps bucket boundaries drift-free across the whole track at 44.1kHz', () => {
		const durationSeconds = 20
		const { decoded, channels } = makeBuffer(44100, durationSeconds)
		const impulseTimes = [0.5, 5.25, 10.0, 14.75, 19.5]
		for (const time of impulseTimes) {
			channels[0][Math.round(time * 44100)] = 1.0
		}

		const { peaks } = computePeaks(decoded)

		for (const time of impulseTimes) {
			const expectedBucket = Math.floor(time * PEAKS_PER_SECOND)
			const window = Array.from(peaks.slice(expectedBucket - 1, expectedBucket + 2))
			expect(Math.max(...window)).toBeCloseTo(1.0)
		}
	})

	it('takes the max absolute amplitude across all channels', () => {
		const { decoded, channels } = makeBuffer(48000, 1, 2)
		// Same bucket, channel 1 louder and negative: envelope must use |sample|.
		channels[0][1200] = 0.3
		channels[1][1201] = -0.8

		const { peaks } = computePeaks(decoded)

		const bucket = Math.floor(1200 / (48000 / PEAKS_PER_SECOND))
		expect(peaks[bucket]).toBeCloseTo(0.8)
	})

	it('includes the final samples of the track in the last bucket', () => {
		const { decoded, channels } = makeBuffer(44100, 2.001)
		channels[0][decoded.length - 1] = 0.7

		const { peaks } = computePeaks(decoded)

		expect(peaks[peaks.length - 1]).toBeCloseTo(0.7)
	})

	it('handles very short audio without producing empty output', () => {
		const { decoded, channels } = makeBuffer(48000, 0.001)
		channels[0][0] = 0.5

		const { peaks } = computePeaks(decoded)

		expect(peaks.length).toBeGreaterThanOrEqual(1)
		expect(peaks[0]).toBeCloseTo(0.5)
	})
})

describe('fetchVocalsPeaks', () => {
  const { fetchVocalsPeaks, AudioNotReadyError, AudioFetchError } = jest.requireActual('@/lib/audio-data')

  const mockJsonResponse = (status: number, body?: unknown) =>
    ({ ok: status >= 200 && status < 300, status, json: async () => body }) as Response

  afterEach(() => {
    jest.restoreAllMocks()
  })

  it('decodes the u8/base64 payload into AudioData', async () => {
    // peaks [0, 128, 255] -> [0, ~0.5, 1]
    const b64 = btoa(String.fromCharCode(0, 128, 255))
    global.fetch = jest.fn(async () =>
      mockJsonResponse(200, {
        peaks_b64: b64,
        encoding: 'u8',
        peaks_per_second: 400,
        duration_seconds: 0.0075,
      })
    ) as unknown as typeof fetch

    const data = await fetchVocalsPeaks('https://api/vocals-peaks')
    expect(data.peaksPerSecond).toBe(400)
    expect(data.duration).toBeCloseTo(0.0075)
    expect(Array.from(data.peaks)).toEqual([0, Math.fround(128 / 255), 1])
  })

  it('throws AudioNotReadyError on 202', async () => {
    global.fetch = jest.fn(async () => mockJsonResponse(202)) as unknown as typeof fetch
    await expect(fetchVocalsPeaks('u')).rejects.toBeInstanceOf(AudioNotReadyError)
  })

  it('throws AudioFetchError with status on non-2xx', async () => {
    global.fetch = jest.fn(async () => mockJsonResponse(404)) as unknown as typeof fetch
    await expect(fetchVocalsPeaks('u')).rejects.toMatchObject({ status: 404 })
  })

  it('treats a malformed payload as a transient server error', async () => {
    global.fetch = jest.fn(async () => mockJsonResponse(200, { nope: true })) as unknown as typeof fetch
    await expect(fetchVocalsPeaks('u')).rejects.toBeInstanceOf(AudioFetchError)
  })
})
