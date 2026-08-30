import { createRef } from 'react'
import { render, screen, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import PreviewVideoSection, { PreviewVideoHandle } from '../PreviewVideoSection'
import type { CorrectionData } from '@/lib/lyrics-review/types'

// The preview flow is async: POST /preview-video returns either "success"
// (video already exists) or "generating" (encode continues on a GCE worker,
// which can cold-start for minutes). In the generating case the component
// polls getPreviewVideoStatus until "ready"/"error" — it must NOT depend on
// the POST staying open (Cloudflare cuts connections at ~100s).

const data = { corrected_segments: [], metadata: {} } as unknown as CorrectionData

function makeApiClient(overrides: Record<string, jest.Mock> = {}) {
  return {
    generatePreviewVideo: jest.fn().mockResolvedValue({
      status: 'success',
      preview_hash: 'hash1',
    }),
    getPreviewVideoStatus: jest.fn().mockResolvedValue({ status: 'generating' }),
    getPreviewVideoUrl: jest.fn((hash: string) => `http://test/preview/${hash}`),
    ...overrides,
  }
}

function renderSection(apiClient: ReturnType<typeof makeApiClient>) {
  return render(
    <PreviewVideoSection apiClient={apiClient} isModalOpen={true} updatedData={data} />
  )
}

const flush = () => act(async () => { await Promise.resolve() })

describe('PreviewVideoSection', () => {
  beforeEach(() => {
    jest.useFakeTimers()
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it('shows the video immediately when the preview already exists (cache hit)', async () => {
    const apiClient = makeApiClient()
    renderSection(apiClient)
    await flush()

    const video = document.querySelector('video')
    expect(video).not.toBeNull()
    expect(video!.src).toBe('http://test/preview/hash1')
    expect(apiClient.getPreviewVideoStatus).not.toHaveBeenCalled()
  })

  it('polls the status endpoint until ready when encoding runs in background', async () => {
    const apiClient = makeApiClient({
      generatePreviewVideo: jest.fn().mockResolvedValue({
        status: 'generating',
        preview_hash: 'hash2',
      }),
      getPreviewVideoStatus: jest
        .fn()
        .mockResolvedValueOnce({ status: 'generating' })
        .mockResolvedValueOnce({ status: 'ready' }),
    })
    renderSection(apiClient)
    await flush()

    // Encoding state shown, no video yet
    expect(screen.getByText('Encoding preview video...')).toBeInTheDocument()
    expect(document.querySelector('video')).toBeNull()

    await act(async () => { await jest.advanceTimersByTimeAsync(3000) })
    expect(apiClient.getPreviewVideoStatus).toHaveBeenCalledTimes(1)
    expect(document.querySelector('video')).toBeNull()

    await act(async () => { await jest.advanceTimersByTimeAsync(3000) })
    expect(apiClient.getPreviewVideoStatus).toHaveBeenCalledTimes(2)
    const video = document.querySelector('video')
    expect(video).not.toBeNull()
    expect(video!.src).toBe('http://test/preview/hash2')
  })

  it('keeps polling through transient status-poll failures', async () => {
    const apiClient = makeApiClient({
      generatePreviewVideo: jest.fn().mockResolvedValue({
        status: 'generating',
        preview_hash: 'hash3',
      }),
      getPreviewVideoStatus: jest
        .fn()
        .mockRejectedValueOnce(new Error('network blip'))
        .mockResolvedValueOnce({ status: 'ready' }),
    })
    renderSection(apiClient)
    await flush()

    await act(async () => { await jest.advanceTimersByTimeAsync(3000) })
    expect(document.querySelector('video')).toBeNull()

    await act(async () => { await jest.advanceTimersByTimeAsync(3000) })
    expect(document.querySelector('video')).not.toBeNull()
  })

  it('surfaces a background encode failure reported by the status endpoint', async () => {
    const apiClient = makeApiClient({
      generatePreviewVideo: jest.fn().mockResolvedValue({
        status: 'generating',
        preview_hash: 'hash4',
      }),
      getPreviewVideoStatus: jest.fn().mockResolvedValue({
        status: 'error',
        message: 'encoder exploded',
      }),
    })
    renderSection(apiClient)
    await flush()

    await act(async () => { await jest.advanceTimersByTimeAsync(3000) })
    expect(screen.getByText('encoder exploded')).toBeInTheDocument()
    // Polling must stop after a terminal error
    await act(async () => { await jest.advanceTimersByTimeAsync(9000) })
    expect(apiClient.getPreviewVideoStatus).toHaveBeenCalledTimes(1)
  })

  it('times out with an error after polling too long', async () => {
    const apiClient = makeApiClient({
      generatePreviewVideo: jest.fn().mockResolvedValue({
        status: 'generating',
        preview_hash: 'hash5',
      }),
      getPreviewVideoStatus: jest.fn().mockResolvedValue({ status: 'generating' }),
    })
    renderSection(apiClient)
    await flush()

    await act(async () => { await jest.advanceTimersByTimeAsync(5 * 60 * 1000 + 5000) })
    expect(
      screen.getByText('The preview video is taking longer than expected. Please try again.')
    ).toBeInTheDocument()
  })

  it('Retry re-runs generation after an error', async () => {
    const apiClient = makeApiClient({
      generatePreviewVideo: jest
        .fn()
        .mockRejectedValueOnce(new Error('boom'))
        .mockResolvedValueOnce({ status: 'success', preview_hash: 'hash6' }),
    })
    renderSection(apiClient)
    await flush()

    expect(screen.getByText('boom')).toBeInTheDocument()

    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    await act(async () => { await jest.advanceTimersByTimeAsync(0) })

    expect(apiClient.generatePreviewVideo).toHaveBeenCalledTimes(2)
    const video = document.querySelector('video')
    expect(video).not.toBeNull()
    expect(video!.src).toBe('http://test/preview/hash6')
  })

  it('shows an error when the server responds without a preview hash', async () => {
    const apiClient = makeApiClient({
      generatePreviewVideo: jest.fn().mockResolvedValue({ status: 'success' }),
    })
    renderSection(apiClient)
    await flush()

    expect(screen.getByText('Failed to generate preview video')).toBeInTheDocument()
  })

  const instrumentalOptions = [
    { id: 'clean', label: 'Clean', audio_url: 'http://test/clean.ogg' },
    { id: 'with_backing', label: 'Backing', audio_url: 'http://test/backing.ogg' },
  ]

  it('shows an audio toggle and a hidden stem player when instrumental options exist', async () => {
    const apiClient = makeApiClient()
    render(
      <PreviewVideoSection
        apiClient={apiClient}
        isModalOpen={true}
        updatedData={data}
        instrumentalOptions={instrumentalOptions as any}
        autoSelection="with_backing"
      />
    )
    await flush()

    expect(screen.getByRole('button', { name: /original \(with vocals\)/i })).toBeInTheDocument()
    // Pill names the auto-selected variant (with_backing).
    expect(screen.getByRole('button', { name: /instrumental \(plus backing vocals\)/i })).toBeInTheDocument()
    // Hidden stem player points at the auto-selected (with_backing) stem.
    const audio = document.querySelector('audio')
    expect(audio).not.toBeNull()
    expect(audio!.src).toBe('http://test/backing.ogg')
  })

  it('does not show the audio toggle when no instrumental options are available', async () => {
    const apiClient = makeApiClient()
    renderSection(apiClient)
    await flush()

    expect(screen.queryByRole('button', { name: /original \(with vocals\)/i })).not.toBeInTheDocument()
    expect(document.querySelector('audio')).toBeNull()
  })

  it('mutes the video when switching to the instrumental track', async () => {
    const apiClient = makeApiClient()
    render(
      <PreviewVideoSection
        apiClient={apiClient}
        isModalOpen={true}
        updatedData={data}
        instrumentalOptions={instrumentalOptions as any}
        autoSelection="clean"
      />
    )
    await flush()

    const video = document.querySelector('video') as HTMLVideoElement
    expect(video.muted).toBe(false)

    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    // autoSelection=clean → pill reads "Instrumental (clean)".
    await user.click(screen.getByRole('button', { name: /instrumental \(clean\)/i }))
    await flush()

    expect(video.muted).toBe(true)
  })

  it('always shows the single Instrumental preview pill (final choice lives in the modal)', async () => {
    const apiClient = makeApiClient()
    render(
      <PreviewVideoSection
        apiClient={apiClient}
        isModalOpen={true}
        updatedData={data}
        instrumentalOptions={instrumentalOptions as any}
        autoSelection="with_backing"
      />
    )
    await flush()

    expect(screen.getByRole('button', { name: /instrumental \(plus backing vocals\)/i })).toBeInTheDocument()
    // No decision pills here anymore — those moved to the modal's radio group.
    expect(screen.queryByRole('button', { name: /instrumental \+ backing vocals/i })).not.toBeInTheDocument()
    // Hidden stem player starts on the auto-selected (with_backing) stem.
    expect((document.querySelector('audio') as HTMLAudioElement).src).toBe('http://test/backing.ogg')
  })

  it('auditionInstrumental swaps the stem, mutes the video, and seeks/plays', async () => {
    const apiClient = makeApiClient()
    const ref = createRef<PreviewVideoHandle>()
    render(
      <PreviewVideoSection
        ref={ref}
        apiClient={apiClient}
        isModalOpen={true}
        updatedData={data}
        instrumentalOptions={instrumentalOptions as any}
        autoSelection="with_backing"
      />
    )
    await flush()

    const video = document.querySelector('video') as HTMLVideoElement
    expect((document.querySelector('audio') as HTMLAudioElement).src).toBe('http://test/backing.ogg')
    expect(video.muted).toBe(false)
    // Pill starts on the backing variant.
    expect(screen.getByRole('button', { name: /instrumental \(plus backing vocals\)/i })).toBeInTheDocument()

    act(() => {
      ref.current!.auditionInstrumental('clean', 5)
    })
    await flush()

    // Stem swapped to clean, video muted (stem plays instead), and it seeked.
    expect((document.querySelector('audio') as HTMLAudioElement).src).toBe('http://test/clean.ogg')
    expect(video.muted).toBe(true)
    expect(video.currentTime).toBe(5)
    // Pill label tracks the new selection.
    expect(screen.getByRole('button', { name: /instrumental \(clean\)/i })).toBeInTheDocument()
  })
})
