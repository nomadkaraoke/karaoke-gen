import { render, screen, within } from '@testing-library/react'
import TranscriptionView from '../TranscriptionView'
import type { CorrectionData, LyricsSegment } from '@/lib/lyrics-review/types'

const seg = (id: string, start: number, end: number, text: string): LyricsSegment => ({
  id,
  text,
  start_time: start,
  end_time: end,
  words: [{ id: `${id}-w`, text, start_time: start, end_time: end }],
})

const data = {
  corrected_segments: [seg('s1', 0, 5, 'intro'), seg('s2', 11, 20, 'verse')],
  original_segments: [],
  reference_lyrics: {},
  anchor_sequences: [],
  gap_sequences: [],
  corrections: [],
} as unknown as CorrectionData

const baseProps = {
  data,
  onElementClick: jest.fn(),
  flashingType: null,
  highlightInfo: null,
  mode: 'edit' as const,
}

describe('TranscriptionView missing-lyrics markers', () => {
  it('renders a marker row between the segments either side of the gap', () => {
    const { container } = render(
      <TranscriptionView
        {...baseProps}
        missingLyricsMarkers={[{ id: 'missing-lyrics-5.00-11.00', beforeSegmentIndex: 1, start: 5, end: 11 }]}
      />
    )
    const marker = screen.getByTestId('missing-lyrics-marker')
    expect(within(marker).getByText('Possible missing lyrics 0:05–0:11')).toBeInTheDocument()
    const text = container.textContent ?? ''
    expect(text.indexOf('intro')).toBeLessThan(text.indexOf('Possible missing lyrics'))
    expect(text.indexOf('Possible missing lyrics')).toBeLessThan(text.indexOf('verse'))
  })

  it('renders a marker after the last segment', () => {
    const { container } = render(
      <TranscriptionView
        {...baseProps}
        missingLyricsMarkers={[{ id: 'm', beforeSegmentIndex: 2, start: 25, end: 31 }]}
      />
    )
    const text = container.textContent ?? ''
    expect(text.indexOf('verse')).toBeLessThan(text.indexOf('Possible missing lyrics 0:25–0:31'))
  })

  it('renders no marker without gaps', () => {
    render(<TranscriptionView {...baseProps} />)
    expect(screen.queryByTestId('missing-lyrics-marker')).not.toBeInTheDocument()
  })
})
