import { act, render, screen } from '@testing-library/react'
import { useContext } from 'react'
import { AudioData, AudioNotReadyError, fetchAudioData as fetchAudioDataImport } from '@/lib/audio-data'
import { VocalsAudioDataLoader, VocalsAudioDataLoaderContext } from '../VocalsAudioDataLoader'

jest.mock('@/lib/audio-data', () => {
  const actual = jest.requireActual('@/lib/audio-data')
  return { ...actual, fetchAudioData: jest.fn() }
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
