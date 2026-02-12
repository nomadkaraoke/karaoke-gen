import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ReplaceAllLyricsModal from '../modals/ReplaceAllLyricsModal'
import { LyricsSegment } from '@/lib/lyrics-review/types'

// Mock the LyricsSynchronizer to avoid complex dependencies
jest.mock('../synchronizer/LyricsSynchronizer', () => {
  return function MockLyricsSynchronizer() {
    return <div data-testid="lyrics-synchronizer">Synchronizer</div>
  }
})

const mockSegments: LyricsSegment[] = [
  {
    id: 'seg1',
    text: 'Hello world',
    start_time: 0,
    end_time: 2,
    words: [
      { id: 'w1', text: 'Hello', start_time: 0, end_time: 1, confidence: 0.9 },
      { id: 'w2', text: 'world', start_time: 1, end_time: 2, confidence: 0.95 },
    ],
  },
  {
    id: 'seg2',
    text: 'Goodbye moon',
    start_time: 3,
    end_time: 5,
    words: [
      { id: 'w3', text: 'Goodbye', start_time: 3, end_time: 4, confidence: 0.88 },
      { id: 'w4', text: 'moon', start_time: 4, end_time: 5, confidence: 0.92 },
    ],
  },
  {
    id: 'seg3',
    text: 'Third line here',
    start_time: 6,
    end_time: 9,
    words: [
      { id: 'w5', text: 'Third', start_time: 6, end_time: 7, confidence: 0.9 },
      { id: 'w6', text: 'line', start_time: 7, end_time: 8, confidence: 0.9 },
      { id: 'w7', text: 'here', start_time: 8, end_time: 9, confidence: 0.9 },
    ],
  },
]

const defaultProps = {
  open: true,
  onClose: jest.fn(),
  onSave: jest.fn(),
  setModalSpacebarHandler: jest.fn(),
  existingSegments: mockSegments,
}

async function openReplaceSegmentsMode() {
  const user = userEvent.setup()
  render(<ReplaceAllLyricsModal {...defaultProps} />)

  // Click "Replace Segment Lyrics" in mode selection
  const replaceSegmentsCard = screen.getByText('Replace Segment Lyrics')
  await user.click(replaceSegmentsCard)
}

describe('Replace Segments Mode', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('shows Replace Segment Lyrics option in mode selection when existing lyrics exist', () => {
    render(<ReplaceAllLyricsModal {...defaultProps} />)

    expect(screen.getByText('Replace Segment Lyrics')).toBeInTheDocument()
    expect(screen.getByText('Recommended for custom lyrics')).toBeInTheDocument()
  })

  it('does not show Replace Segment Lyrics when no existing lyrics', () => {
    render(<ReplaceAllLyricsModal {...defaultProps} existingSegments={[]} />)

    expect(screen.queryByText('Replace Segment Lyrics')).not.toBeInTheDocument()
  })

  it('renders textarea pre-filled with existing segment text', async () => {
    await openReplaceSegmentsMode()

    const textarea = screen.getByRole('textbox')
    expect(textarea).toHaveValue('Hello world\nGoodbye moon\nThird line here')
  })

  it('shows correct matching line count', async () => {
    await openReplaceSegmentsMode()

    expect(screen.getByText('3/3 lines')).toBeInTheDocument()
  })

  it('Apply button is enabled when line count matches', async () => {
    await openReplaceSegmentsMode()

    const applyButton = screen.getByRole('button', { name: 'Apply' })
    expect(applyButton).not.toBeDisabled()
  })

  it('disables Apply when line count differs (too many)', async () => {
    const user = userEvent.setup()
    await openReplaceSegmentsMode()

    const textarea = screen.getByRole('textbox')
    await user.clear(textarea)
    await user.type(textarea, 'line 1\nline 2\nline 3\nline 4')

    const applyButton = screen.getByRole('button', { name: 'Apply' })
    expect(applyButton).toBeDisabled()
    expect(screen.getByText(/1 too many/)).toBeInTheDocument()
  })

  it('disables Apply when line count differs (too few)', async () => {
    const user = userEvent.setup()
    await openReplaceSegmentsMode()

    const textarea = screen.getByRole('textbox')
    await user.clear(textarea)
    await user.type(textarea, 'line 1\nline 2')

    const applyButton = screen.getByRole('button', { name: 'Apply' })
    expect(applyButton).toBeDisabled()
    expect(screen.getByText(/1 too few/)).toBeInTheDocument()
  })

  it('shows mismatch error message with correct counts', async () => {
    const user = userEvent.setup()
    await openReplaceSegmentsMode()

    const textarea = screen.getByRole('textbox')
    await user.clear(textarea)
    await user.type(textarea, 'only one line')

    expect(screen.getByText(/expected 3, got 1/)).toBeInTheDocument()
  })

  it('calls onSave with unchanged segments when no text is modified', async () => {
    const user = userEvent.setup()
    await openReplaceSegmentsMode()

    const applyButton = screen.getByRole('button', { name: 'Apply' })
    await user.click(applyButton)

    expect(defaultProps.onSave).toHaveBeenCalledTimes(1)
    const savedSegments = defaultProps.onSave.mock.calls[0][0]
    expect(savedSegments).toHaveLength(3)

    // Unchanged segments should have same words (deep copy)
    expect(savedSegments[0].text).toBe('Hello world')
    expect(savedSegments[0].words).toHaveLength(2)
    expect(savedSegments[0].words[0].text).toBe('Hello')
    expect(savedSegments[0].words[1].text).toBe('world')
    // Preserved original timing
    expect(savedSegments[0].words[0].start_time).toBe(0)
    expect(savedSegments[0].words[0].end_time).toBe(1)
  })

  it('creates new words with distributed timing for changed lines', async () => {
    const user = userEvent.setup()
    await openReplaceSegmentsMode()

    const textarea = screen.getByRole('textbox')
    await user.clear(textarea)
    await user.type(textarea, 'Hello world\nNew custom lyrics here\nThird line here')

    const applyButton = screen.getByRole('button', { name: 'Apply' })
    await user.click(applyButton)

    expect(defaultProps.onSave).toHaveBeenCalledTimes(1)
    const savedSegments = defaultProps.onSave.mock.calls[0][0]

    // First segment unchanged
    expect(savedSegments[0].words[0].text).toBe('Hello')
    expect(savedSegments[0].words[0].start_time).toBe(0)

    // Second segment changed — new words with distributed timing over 3-5s
    expect(savedSegments[1].text).toBe('New custom lyrics here')
    expect(savedSegments[1].words).toHaveLength(4)
    expect(savedSegments[1].words[0].text).toBe('New')
    expect(savedSegments[1].words[0].start_time).toBe(3)
    expect(savedSegments[1].words[3].text).toBe('here')
    expect(savedSegments[1].words[3].end_time).toBe(5)

    // Third segment unchanged
    expect(savedSegments[2].words[0].text).toBe('Third')
  })

  it('preserves segment id and timing for changed lines', async () => {
    const user = userEvent.setup()
    await openReplaceSegmentsMode()

    const textarea = screen.getByRole('textbox')
    await user.clear(textarea)
    await user.type(textarea, 'Hello world\nCompletely different\nThird line here')

    const applyButton = screen.getByRole('button', { name: 'Apply' })
    await user.click(applyButton)

    const savedSegments = defaultProps.onSave.mock.calls[0][0]

    // Changed segment keeps original id and timing boundaries
    expect(savedSegments[1].id).toBe('seg2')
    expect(savedSegments[1].start_time).toBe(3)
    expect(savedSegments[1].end_time).toBe(5)
  })

  it('calls onClose after applying', async () => {
    const user = userEvent.setup()
    await openReplaceSegmentsMode()

    const applyButton = screen.getByRole('button', { name: 'Apply' })
    await user.click(applyButton)

    expect(defaultProps.onClose).toHaveBeenCalled()
  })

  it('back button returns to mode selection', async () => {
    const user = userEvent.setup()
    await openReplaceSegmentsMode()

    // Should be in replace segments mode
    expect(screen.getByText('Edit lyrics line by line', { exact: false })).toBeInTheDocument()

    // Click back arrow
    const backButton = screen.getAllByRole('button').find(
      (btn) => btn.querySelector('svg.lucide-arrow-left')
    )
    expect(backButton).toBeTruthy()
    await user.click(backButton!)

    // Should be back in mode selection
    expect(screen.getByText('Edit All Lyrics')).toBeInTheDocument()
    expect(screen.getByText('Re-sync Existing Lyrics')).toBeInTheDocument()
    expect(screen.getByText('Replace Segment Lyrics')).toBeInTheDocument()
  })

  it('cancel button closes the modal', async () => {
    const user = userEvent.setup()
    await openReplaceSegmentsMode()

    const cancelButton = screen.getByRole('button', { name: 'Cancel' })
    await user.click(cancelButton)

    expect(defaultProps.onClose).toHaveBeenCalled()
  })

  it('does not show Replace Segment Lyrics when segments have no words', () => {
    const emptyWordSegments: LyricsSegment[] = [
      { id: 's1', text: 'test', start_time: 0, end_time: 1, words: [] },
    ]
    render(<ReplaceAllLyricsModal {...defaultProps} existingSegments={emptyWordSegments} />)

    expect(screen.queryByText('Replace Segment Lyrics')).not.toBeInTheDocument()
  })

  it('handles changing empty segment text to non-empty', async () => {
    const user = userEvent.setup()
    const segmentsWithEmpty: LyricsSegment[] = [
      {
        id: 'seg1',
        text: '',
        start_time: 0,
        end_time: 2,
        words: [{ id: 'w1', text: '', start_time: 0, end_time: 2, confidence: 1 }],
      },
      {
        id: 'seg2',
        text: 'Hello world',
        start_time: 3,
        end_time: 5,
        words: [
          { id: 'w2', text: 'Hello', start_time: 3, end_time: 4, confidence: 1 },
          { id: 'w3', text: 'world', start_time: 4, end_time: 5, confidence: 1 },
        ],
      },
    ]

    render(<ReplaceAllLyricsModal {...defaultProps} existingSegments={segmentsWithEmpty} />)

    const replaceSegmentsCard = screen.getByText('Replace Segment Lyrics')
    await user.click(replaceSegmentsCard)

    const textarea = screen.getByRole('textbox')
    await user.clear(textarea)
    await user.type(textarea, 'New first line\nHello world')

    const applyButton = screen.getByRole('button', { name: 'Apply' })
    await user.click(applyButton)

    const savedSegments = defaultProps.onSave.mock.calls[0][0]
    expect(savedSegments[0].text).toBe('New first line')
    expect(savedSegments[0].words).toHaveLength(3)
    expect(savedSegments[0].words[0].start_time).toBe(0)
    expect(savedSegments[0].words[2].end_time).toBeCloseTo(2)
  })

  it('deep copies unchanged segments (no mutation)', async () => {
    const user = userEvent.setup()
    await openReplaceSegmentsMode()

    const applyButton = screen.getByRole('button', { name: 'Apply' })
    await user.click(applyButton)

    const savedSegments = defaultProps.onSave.mock.calls[0][0]

    // Verify deep copy — different object references
    expect(savedSegments[0]).not.toBe(mockSegments[0])
    expect(savedSegments[0].words).not.toBe(mockSegments[0].words)
    expect(savedSegments[0].words[0]).not.toBe(mockSegments[0].words[0])
  })
})
