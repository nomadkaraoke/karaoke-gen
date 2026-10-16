import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import TranscriptionView from '../TranscriptionView'
import { VocalsAudioDataLoaderContext } from '../VocalsAudioDataLoader'
import type { CorrectionData } from '@/lib/lyrics-review/types'

const data = {
  corrected_segments: [
    { id: 's1', text: 'one', start_time: 0, end_time: 2, words: [{ id: 'w1', text: 'one', start_time: 0, end_time: 2 }] },
    { id: 's2', text: 'two', start_time: 3, end_time: 5, singer: 2, words: [{ id: 'w2', text: 'two', start_time: 3, end_time: 5 }] },
  ],
  original_segments: [],
  reference_lyrics: {},
  anchor_sequences: [],
  gap_sequences: [],
  corrections: [],
} as unknown as CorrectionData

const renderView = (isDuet: boolean, onSegmentSingerChange = jest.fn()) =>
  render(
    <VocalsAudioDataLoaderContext.Provider value={{ audioData: null, status: 'ready' }}>
      <TranscriptionView
        data={data}
        onElementClick={jest.fn()}
        flashingType={null}
        highlightInfo={null}
        mode="edit"
        viewMode="waveforms"
        isDuet={isDuet}
        onSegmentSingerChange={onSegmentSingerChange}
      />
    </VocalsAudioDataLoaderContext.Provider>
  )

describe('TranscriptionView Waveforms mode in duet', () => {
  it('shows a singer chip per line and changes the singer on click', async () => {
    const onSegmentSingerChange = jest.fn()
    renderView(true, onSegmentSingerChange)
    const chips = screen.getAllByText('●').map((dot) => dot.closest('button')!)
    expect(chips.map((c) => c.textContent)).toEqual(['●1', '●2'])
    const chip2 = chips[1]
    await userEvent.click(chip2)
    expect(onSegmentSingerChange).toHaveBeenCalledWith(1, 0)
  })

  it('shows no singer chips when duet mode is off', () => {
    renderView(false)
    expect(screen.queryByText('●')).not.toBeInTheDocument()
  })
})
