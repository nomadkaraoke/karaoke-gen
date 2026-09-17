import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import WaveformSegmentRow from '../WaveformSegmentRow'
import { LyricsSegment } from '@/lib/lyrics-review/types'

const timedSegment: LyricsSegment = {
  id: 's0',
  text: 'No looking back',
  start_time: 140.6,
  end_time: 142.6,
  words: [
    { id: 'w1', text: 'No', start_time: 140.6, end_time: 141.0, confidence: 1 },
    { id: 'w2', text: 'looking', start_time: 141.0, end_time: 142.3, confidence: 1 },
    { id: 'w3', text: 'back', start_time: 142.4, end_time: 142.6, confidence: 1 },
  ],
}

function renderRow(overrides: Partial<React.ComponentProps<typeof WaveformSegmentRow>> = {}) {
  const props = {
    segment: timedSegment,
    segmentIndex: 0,
    contextWords: [],
    wordDecorations: new Map(),
    onCommit: jest.fn(),
    onPlaySegment: jest.fn(),
    onEditSegment: jest.fn(),
    onDeleteSegment: jest.fn(),
    ...overrides,
  }
  render(<WaveformSegmentRow {...props} />)
  return props
}

describe('WaveformSegmentRow', () => {
  it('opens the edit modal when the segment index is clicked', async () => {
    const user = userEvent.setup()
    const props = renderRow()
    await user.click(screen.getByTitle('Edit segment 0'))
    expect(props.onEditSegment).toHaveBeenCalledWith(0)
  })

  it('deletes the segment when the trash control is clicked', async () => {
    const user = userEvent.setup()
    const props = renderRow()
    await user.click(screen.getByTitle('Delete segment'))
    expect(props.onDeleteSegment).toHaveBeenCalledWith(0)
  })

  it('renders a word bar per timed word', () => {
    renderRow()
    // Word text is rendered inside the draggable bars.
    expect(screen.getByText('No')).toBeInTheDocument()
    expect(screen.getByText('looking')).toBeInTheDocument()
    expect(screen.getByText('back')).toBeInTheDocument()
  })

  it('shows a clickable fallback (no timeline) for an untimed segment', async () => {
    const user = userEvent.setup()
    const untimed: LyricsSegment = {
      id: 's1',
      text: 'untimed line',
      start_time: null,
      end_time: null,
      words: [{ id: 'u1', text: 'untimed line', start_time: null, end_time: null, confidence: 1 }],
    }
    const props = renderRow({ segment: untimed, segmentIndex: 4 })
    const fallback = screen.getByText('untimed line')
    await user.click(fallback)
    expect(props.onEditSegment).toHaveBeenCalledWith(4)
  })
})
