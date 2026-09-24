import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ReviewChangesModal from '../modals/ReviewChangesModal'
import type { CorrectionData } from '@/lib/lyrics-review/types'

const mockAudition = jest.fn()
jest.mock('../PreviewVideoSection', () => {
  const React = require('react')
  return {
    __esModule: true,
    default: React.forwardRef(function MockPreview(_props: unknown, ref: unknown) {
      React.useImperativeHandle(ref, () => ({ auditionInstrumental: mockAudition }))
      return <div data-testid="preview-video">Preview</div>
    }),
  }
})

// Stand-in for the canvas waveform: a button that "clicks" at 12.5 s.
jest.mock('../BackingVocalsWaveform', () => ({
  __esModule: true,
  default: ({ onSeek }: { onSeek: (t: number) => void }) => (
    <button onClick={() => onSeek(12.5)}>backing-waveform</button>
  ),
}))

const bothStems = [
  { id: 'clean', label: 'Clean', audio_url: 'http://x/clean.ogg' },
  { id: 'with_backing', label: 'Backing', audio_url: 'http://x/backing.ogg' },
]

function makeData(): CorrectionData {
  return {
    original_segments: [],
    reference_lyrics: {},
    anchor_sequences: [],
    gap_sequences: [],
    resized_segments: [],
    corrections_made: 0,
    confidence: 1,
    corrections: [],
    corrected_segments: [{ text: 'Hello', words: [], start_time: 0, end_time: 1 }],
    metadata: {},
    instrumental_options: bothStems,
  } as unknown as CorrectionData
}

describe('ReviewChangesModal backing-vocals waveform click', () => {
  beforeEach(() => mockAudition.mockClear())

  it('selects "Instrumental + backing vocals" and auditions it from the clicked time', async () => {
    const user = userEvent.setup()
    const onSelectInstrumental = jest.fn()
    render(
      <ReviewChangesModal
        open
        onClose={jest.fn()}
        onSubmit={jest.fn()}
        apiClient={{ getWaveformData: jest.fn() } as any}
        completesReview
        offerInlineChoice
        recommendedSelection="clean"
        currentSelection="clean"
        onSelectInstrumental={onSelectInstrumental}
        data={makeData()}
      />
    )

    await user.click(screen.getByText('backing-waveform'))

    expect(onSelectInstrumental).toHaveBeenCalledWith('with_backing')
    expect(mockAudition).toHaveBeenCalledWith('with_backing', 12.5)
  })
})
