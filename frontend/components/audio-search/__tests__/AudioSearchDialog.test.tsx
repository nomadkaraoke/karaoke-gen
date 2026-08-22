import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { AudioSearchDialog } from '../AudioSearchDialog'
import { api, ApiError } from '@/lib/api'

jest.mock('@/lib/api', () => {
  class ApiError extends Error {
    status: number
    constructor(message: string, status: number) {
      super(message)
      this.status = status
    }
  }
  return {
    ApiError,
    api: {
      getAudioSearchResults: jest.fn(),
      selectAudioResult: jest.fn(),
      researchAudio: jest.fn(),
      provideUrlForJob: jest.fn(),
      attachUploadToJob: jest.fn(),
    },
  }
})

const mockApi = api as jest.Mocked<typeof api>

const aResult = (index = 0) => ({
  index,
  title: 'The View From the Afternoon',
  artist: 'Arctic Monkeys',
  provider: 'RED',
  is_lossless: true,
  seeders: 30,
  target_file: 'The View From the Afternoon.flac',
  quality_data: {},
})

describe('AudioSearchDialog recovery UX', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('renders no-results help and the refine bar when the job has no sources', async () => {
    mockApi.getAudioSearchResults.mockResolvedValue({
      status: 'success', job_id: 'j1', results: [], total_results: 0,
      artist: 'Arctic Monkeys', title: 'The View From the Afternoon',
    } as any)

    render(
      <AudioSearchDialog jobId="j1" open onClose={jest.fn()} onSelect={jest.fn()}
        searchArtist="Arctic Monkeys" searchTitle="The View From the Afternoon" />
    )

    await screen.findByText('No audio sources found')
    expect(screen.getByText(/couldn't find any audio sources/i)).toBeInTheDocument()
    // Editable terms seeded from the API response
    expect(screen.getByDisplayValue('Arctic Monkeys')).toBeInTheDocument()
    expect(screen.getByDisplayValue('The View From the Afternoon')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /search again/i })).toBeInTheDocument()
  })

  it('re-searches with edited terms and shows the fresh results', async () => {
    mockApi.getAudioSearchResults.mockResolvedValue({
      status: 'success', job_id: 'j1', results: [], total_results: 0,
      artist: 'Arctic Monkeys', title: 'The View From teh Afternoon',
    } as any)
    mockApi.researchAudio.mockResolvedValue({
      status: 'awaiting_selection', job_id: 'j1', results: [aResult(0)], total_results: 1, results_count: 1,
    } as any)

    render(
      <AudioSearchDialog jobId="j1" open onClose={jest.fn()} onSelect={jest.fn()}
        searchArtist="Arctic Monkeys" searchTitle="The View From teh Afternoon" />
    )

    await screen.findByText('No audio sources found')

    const titleInput = screen.getByDisplayValue('The View From teh Afternoon')
    fireEvent.change(titleInput, { target: { value: 'The View From the Afternoon' } })
    fireEvent.click(screen.getByRole('button', { name: /search again/i }))

    await waitFor(() => {
      expect(mockApi.researchAudio).toHaveBeenCalledWith('j1', {
        artist: 'Arctic Monkeys', title: 'The View From the Afternoon',
      })
    })
    // Results now render (RED category chip + a Select button)
    await waitFor(() => expect(screen.getByRole('button', { name: /select/i })).toBeInTheDocument())
  })

  it('submits a provided URL and advances the job', async () => {
    mockApi.getAudioSearchResults.mockResolvedValue({
      status: 'success', job_id: 'j1', results: [], total_results: 0,
    } as any)
    mockApi.provideUrlForJob.mockResolvedValue({ status: 'success', job_id: 'j1', message: 'ok' } as any)
    const onSelect = jest.fn()
    const onClose = jest.fn()

    render(
      <AudioSearchDialog jobId="j1" open onClose={onClose} onSelect={onSelect}
        searchArtist="Arctic Monkeys" searchTitle="Riot Van" />
    )

    await screen.findByText('No audio sources found')
    fireEvent.click(screen.getByRole('button', { name: /paste url/i }))
    const urlInput = await screen.findByPlaceholderText(/youtube\.com/i)
    fireEvent.change(urlInput, { target: { value: 'https://youtube.com/watch?v=abc' } })
    fireEvent.click(screen.getByRole('button', { name: /use this url/i }))

    await waitFor(() =>
      expect(mockApi.provideUrlForJob).toHaveBeenCalledWith('j1', 'https://youtube.com/watch?v=abc')
    )
    await waitFor(() => expect(onSelect).toHaveBeenCalled())
    expect(onClose).toHaveBeenCalled()
  })

  it('treats a 400 (no cached results) as an empty state, not a red error', async () => {
    mockApi.getAudioSearchResults.mockRejectedValue(new ApiError('No search results available', 400))

    render(
      <AudioSearchDialog jobId="j1" open onClose={jest.fn()} onSelect={jest.fn()}
        searchArtist="Arctic Monkeys" searchTitle="The View From the Afternoon" />
    )

    await screen.findByText('No audio sources found')
    expect(screen.queryByText('No search results available')).not.toBeInTheDocument()
  })

  it('surfaces a non-400 (network/500) failure as an error banner', async () => {
    mockApi.getAudioSearchResults.mockRejectedValue(new ApiError('Server exploded', 500))

    render(
      <AudioSearchDialog jobId="j1" open onClose={jest.fn()} onSelect={jest.fn()}
        searchArtist="Arctic Monkeys" searchTitle="The View From the Afternoon" />
    )

    // Empty state still renders (refine bar available), but the real error shows too.
    await screen.findByText('No audio sources found')
    expect(screen.getByText('Server exploded')).toBeInTheDocument()
  })
})
