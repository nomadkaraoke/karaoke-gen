import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import TranscriptionView from '../TranscriptionView'
import { VocalsAudioDataLoaderContext, VocalsAudioStatus } from '../VocalsAudioDataLoader'
import type { CorrectionData } from '@/lib/lyrics-review/types'

const data = {
  corrected_segments: [
    { id: 's1', text: 'intro', start_time: 0, end_time: 5, words: [{ id: 'w1', text: 'intro', start_time: 0, end_time: 5 }] },
  ],
  original_segments: [],
  reference_lyrics: {},
  anchor_sequences: [],
  gap_sequences: [],
  corrections: [],
} as unknown as CorrectionData

const renderWithStatus = (status: VocalsAudioStatus) =>
  render(
    <VocalsAudioDataLoaderContext.Provider value={{ audioData: null, status }}>
      <TranscriptionView
        data={data}
        onElementClick={jest.fn()}
        flashingType={null}
        highlightInfo={null}
        mode="edit"
        viewMode="simple"
      />
    </VocalsAudioDataLoaderContext.Provider>
  )

describe('TranscriptionView Waveforms toggle while separation runs', () => {
  it('shows a spinner with an explanatory tooltip while the vocal stem is being separated', async () => {
    renderWithStatus('separating')
    expect(screen.getByTestId('waveforms-separating-spinner')).toBeInTheDocument()

    await userEvent.hover(screen.getByRole('radio', { name: 'waveforms view' }))
    expect(
      (await screen.findAllByText(/Separating vocals from the music/)).length
    ).toBeGreaterThan(0)
  })

  it.each<VocalsAudioStatus>(['loading', 'ready', 'failed', 'idle'])('shows the normal icon when status is %s', (status) => {
    renderWithStatus(status)
    expect(screen.queryByTestId('waveforms-separating-spinner')).not.toBeInTheDocument()
  })
})
