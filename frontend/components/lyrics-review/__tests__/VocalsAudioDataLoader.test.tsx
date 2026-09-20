import { act, render, screen } from '@testing-library/react'
import { useContext } from 'react'
import { AudioData, AudioFetchError, AudioNotReadyError, fetchAudioData as fetchAudioDataImport } from '@/lib/audio-data'
import { VocalsAudioDataLoader, VocalsAudioDataLoaderContext } from '../VocalsAudioDataLoader'

jest.mock('@/lib/audio-data', () => {
  const actual = jest.requireActual('@/lib/audio-data')
  return { ...actual, fetchAudioData: jest.fn(), fetchVocalsPeaks: jest.fn() }
})

const fetchAudioData = fetchAudioDataImport as jest.Mock

const AUDIO_DATA: AudioData = {
  duration: 120,
  peaks: new Float32Array([0.5]),
  peaksPerSecond: 400,
}

const Probe = () => {
  const { audioData } = useContext(VocalsAudioDataLoaderContext)
  return <div data-testid="probe">{audioData ? 'loaded' : 'empty'}</div>
}

// Flush the pending promise chain inside the loader's .then/.catch handlers.
const flushPromises = () => act(async () => {})

describe('VocalsAudioDataLoader', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    jest.useFakeTimers()
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it('provides audio data when the fetch succeeds', async () => {
    fetchAudioData.mockResolvedValue(AUDIO_DATA)

    render(
      <VocalsAudioDataLoader audioUrl="https://api/audio/vocals">
        <Probe />
      </VocalsAudioDataLoader>
    )
    await flushPromises()

    expect(screen.getByTestId('probe')).toHaveTextContent('loaded')
    expect(fetchAudioData).toHaveBeenCalledTimes(1)
  })

  it('retries while separation is in progress (202), then loads', async () => {
    // Separation runs in the background during lyrics review — the stem often
    // doesn't exist on first load. The loader must poll, not give up.
    fetchAudioData
      .mockRejectedValueOnce(new AudioNotReadyError())
      .mockRejectedValueOnce(new AudioNotReadyError())
      .mockResolvedValue(AUDIO_DATA)

    render(
      <VocalsAudioDataLoader audioUrl="https://api/audio/vocals">
        <Probe />
      </VocalsAudioDataLoader>
    )
    await flushPromises()
    expect(screen.getByTestId('probe')).toHaveTextContent('empty')

    await act(async () => {
      jest.advanceTimersByTime(15_000)
    })
    expect(screen.getByTestId('probe')).toHaveTextContent('empty')

    await act(async () => {
      jest.advanceTimersByTime(15_000)
    })
    expect(fetchAudioData).toHaveBeenCalledTimes(3)
    expect(screen.getByTestId('probe')).toHaveTextContent('loaded')
  })

  it('does not retry on a terminal error (e.g. 404: job has no vocal stem)', async () => {
    fetchAudioData.mockRejectedValue(new Error('Failed to fetch vocals audio: 404'))
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {})

    render(
      <VocalsAudioDataLoader audioUrl="https://api/audio/vocals">
        <Probe />
      </VocalsAudioDataLoader>
    )
    await flushPromises()

    await act(async () => {
      jest.advanceTimersByTime(60_000)
    })
    expect(fetchAudioData).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('probe')).toHaveTextContent('empty')
    expect(consoleError).toHaveBeenCalled()

    consoleError.mockRestore()
  })

  it('retries a transient failure (500 under load) with backoff, then loads', async () => {
    // Ten review tabs at once can briefly 500 this endpoint; giving up silently
    // left the Waveforms view stripless until a manual reload.
    fetchAudioData
      .mockRejectedValueOnce(new AudioFetchError(500))
      .mockResolvedValue(AUDIO_DATA)
    const consoleWarn = jest.spyOn(console, 'warn').mockImplementation(() => {})

    render(
      <VocalsAudioDataLoader audioUrl="https://api/audio/vocals">
        <Probe />
      </VocalsAudioDataLoader>
    )
    await flushPromises()
    expect(screen.getByTestId('probe')).toHaveTextContent('empty')

    await act(async () => {
      jest.advanceTimersByTime(5_000)
    })
    expect(fetchAudioData).toHaveBeenCalledTimes(2)
    expect(screen.getByTestId('probe')).toHaveTextContent('loaded')

    consoleWarn.mockRestore()
  })

  it('gives up after exhausting transient retries', async () => {
    fetchAudioData.mockRejectedValue(new AudioFetchError(503))
    const consoleWarn = jest.spyOn(console, 'warn').mockImplementation(() => {})
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {})

    render(
      <VocalsAudioDataLoader audioUrl="https://api/audio/vocals">
        <Probe />
      </VocalsAudioDataLoader>
    )
    await flushPromises()

    // 5s + 15s + 30s + 60s of backoff = 4 retries after the initial attempt.
    for (const delay of [5_000, 15_000, 30_000, 60_000, 120_000]) {
      await act(async () => {
        jest.advanceTimersByTime(delay)
      })
    }
    expect(fetchAudioData).toHaveBeenCalledTimes(5)
    expect(screen.getByTestId('probe')).toHaveTextContent('empty')
    expect(consoleError).toHaveBeenCalled()

    consoleWarn.mockRestore()
    consoleError.mockRestore()
  })

  it('does not retry a 404 (AudioFetchError, terminal)', async () => {
    fetchAudioData.mockRejectedValue(new AudioFetchError(404))
    const consoleError = jest.spyOn(console, 'error').mockImplementation(() => {})

    render(
      <VocalsAudioDataLoader audioUrl="https://api/audio/vocals">
        <Probe />
      </VocalsAudioDataLoader>
    )
    await flushPromises()

    await act(async () => {
      jest.advanceTimersByTime(120_000)
    })
    expect(fetchAudioData).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('probe')).toHaveTextContent('empty')

    consoleError.mockRestore()
  })

  it('stops polling on unmount', async () => {
    fetchAudioData.mockRejectedValue(new AudioNotReadyError())

    const { unmount } = render(
      <VocalsAudioDataLoader audioUrl="https://api/audio/vocals">
        <Probe />
      </VocalsAudioDataLoader>
    )
    await flushPromises()
    unmount()

    await act(async () => {
      jest.advanceTimersByTime(120_000)
    })
    expect(fetchAudioData).toHaveBeenCalledTimes(1)
  })
})

describe('VocalsAudioDataLoader peaks-first behavior', () => {
  const audioDataLib = jest.requireMock('@/lib/audio-data')

  beforeEach(() => {
    jest.clearAllMocks()
    jest.useFakeTimers()
    audioDataLib.fetchVocalsPeaks = jest.fn()
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it('prefers the peaks endpoint and never downloads the full audio', async () => {
    audioDataLib.fetchVocalsPeaks.mockResolvedValue(AUDIO_DATA)

    render(
      <VocalsAudioDataLoader audioUrl="https://api/audio/vocals" peaksUrl="https://api/vocals-peaks">
        <Probe />
      </VocalsAudioDataLoader>
    )
    await flushPromises()

    expect(screen.getByTestId('probe')).toHaveTextContent('loaded')
    expect(audioDataLib.fetchVocalsPeaks).toHaveBeenCalledTimes(1)
    expect(fetchAudioData).not.toHaveBeenCalled()
  })

  it('falls back to full audio decode when peaks fail terminally', async () => {
    audioDataLib.fetchVocalsPeaks.mockRejectedValue(new AudioFetchError(404))
    fetchAudioData.mockResolvedValue(AUDIO_DATA)
    const consoleWarn = jest.spyOn(console, 'warn').mockImplementation(() => {})

    render(
      <VocalsAudioDataLoader audioUrl="https://api/audio/vocals" peaksUrl="https://api/vocals-peaks">
        <Probe />
      </VocalsAudioDataLoader>
    )
    await flushPromises()

    expect(fetchAudioData).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('probe')).toHaveTextContent('loaded')
    consoleWarn.mockRestore()
  })

  it('retries peaks once on a transient error, then falls back to audio', async () => {
    audioDataLib.fetchVocalsPeaks.mockRejectedValue(new AudioFetchError(503))
    fetchAudioData.mockResolvedValue(AUDIO_DATA)
    const consoleWarn = jest.spyOn(console, 'warn').mockImplementation(() => {})

    render(
      <VocalsAudioDataLoader audioUrl="https://api/audio/vocals" peaksUrl="https://api/vocals-peaks">
        <Probe />
      </VocalsAudioDataLoader>
    )
    await flushPromises()
    expect(audioDataLib.fetchVocalsPeaks).toHaveBeenCalledTimes(1)

    // One quick retry of peaks...
    await act(async () => {
      jest.advanceTimersByTime(5_000)
    })
    expect(audioDataLib.fetchVocalsPeaks).toHaveBeenCalledTimes(2)
    // ...then immediate fallback to the audio path (no more peaks calls).
    expect(fetchAudioData).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId('probe')).toHaveTextContent('loaded')

    consoleWarn.mockRestore()
  })

  it('polls peaks on 202 while separation is running', async () => {
    audioDataLib.fetchVocalsPeaks
      .mockRejectedValueOnce(new AudioNotReadyError())
      .mockResolvedValue(AUDIO_DATA)

    render(
      <VocalsAudioDataLoader audioUrl="https://api/audio/vocals" peaksUrl="https://api/vocals-peaks">
        <Probe />
      </VocalsAudioDataLoader>
    )
    await flushPromises()
    expect(screen.getByTestId('probe')).toHaveTextContent('empty')

    await act(async () => {
      jest.advanceTimersByTime(15_000)
    })
    expect(screen.getByTestId('probe')).toHaveTextContent('loaded')
    expect(fetchAudioData).not.toHaveBeenCalled()
  })

  it('works with only audioUrl (local mode without a peaks endpoint)', async () => {
    fetchAudioData.mockResolvedValue(AUDIO_DATA)

    render(
      <VocalsAudioDataLoader audioUrl="https://api/audio/vocals">
        <Probe />
      </VocalsAudioDataLoader>
    )
    await flushPromises()

    expect(screen.getByTestId('probe')).toHaveTextContent('loaded')
    expect(audioDataLib.fetchVocalsPeaks).not.toHaveBeenCalled()
  })
})
