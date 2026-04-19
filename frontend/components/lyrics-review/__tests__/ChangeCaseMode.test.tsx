import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ReplaceAllLyricsModal from '../modals/ReplaceAllLyricsModal'
import { LyricsSegment } from '@/lib/lyrics-review/types'

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
    text: 'Goodbye MOON',
    start_time: 3,
    end_time: 5,
    words: [
      { id: 'w3', text: 'Goodbye', start_time: 3, end_time: 4, confidence: 0.88 },
      { id: 'w4', text: 'MOON', start_time: 4, end_time: 5, confidence: 0.92 },
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

async function openChangeCaseMode() {
  const user = userEvent.setup()
  render(<ReplaceAllLyricsModal {...defaultProps} />)
  await user.click(screen.getByText('Change Case'))
  return user
}

describe('Change Case Mode', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('shows Change Case option in mode selection when existing lyrics exist', () => {
    render(<ReplaceAllLyricsModal {...defaultProps} />)
    expect(screen.getByText('Change Case')).toBeInTheDocument()
    expect(screen.getByText(/Convert all lyrics to/i)).toBeInTheDocument()
  })

  it('does not show Change Case option when no existing lyrics', () => {
    render(<ReplaceAllLyricsModal {...defaultProps} existingSegments={[]} />)
    expect(screen.queryByText('Change Case')).not.toBeInTheDocument()
  })

  it('renders all four case buttons when opened', async () => {
    await openChangeCaseMode()
    expect(screen.getByRole('button', { name: /^UPPERCASE/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^lowercase/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Title Case/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Sentence case/ })).toBeInTheDocument()
  })

  it('shows preview of UPPERCASE by default', async () => {
    await openChangeCaseMode()
    expect(screen.getByText(/HELLO WORLD/)).toBeInTheDocument()
    expect(screen.getByText(/GOODBYE MOON/)).toBeInTheDocument()
  })

  it('updates preview when lowercase is selected', async () => {
    const user = await openChangeCaseMode()
    await user.click(screen.getByRole('button', { name: /^lowercase/ }))
    expect(screen.getByText(/hello world/)).toBeInTheDocument()
    expect(screen.getByText(/goodbye moon/)).toBeInTheDocument()
  })

  it('updates preview when Title Case is selected', async () => {
    const user = await openChangeCaseMode()
    await user.click(screen.getByRole('button', { name: /^Title Case/ }))
    expect(screen.getByText(/Hello World/)).toBeInTheDocument()
    expect(screen.getByText(/Goodbye Moon/)).toBeInTheDocument()
  })

  it('calls onSave with UPPERCASE segments and change_case meta', async () => {
    const onSave = jest.fn()
    const user = userEvent.setup()
    render(<ReplaceAllLyricsModal {...defaultProps} onSave={onSave} />)
    await user.click(screen.getByText('Change Case'))
    await user.click(screen.getByRole('button', { name: /^Apply/ }))

    expect(onSave).toHaveBeenCalledTimes(1)
    const [segments, meta] = onSave.mock.calls[0]
    expect(segments[0].text).toBe('HELLO WORLD')
    expect(segments[0].words.map((w: { text: string }) => w.text)).toEqual(['HELLO', 'WORLD'])
    expect(segments[1].text).toBe('GOODBYE MOON')
    expect(meta).toEqual({ operation: 'change_case', details: { case_type: 'upper' } })
  })

  it('preserves word start_time and end_time on apply', async () => {
    const onSave = jest.fn()
    const user = userEvent.setup()
    render(<ReplaceAllLyricsModal {...defaultProps} onSave={onSave} />)
    await user.click(screen.getByText('Change Case'))
    await user.click(screen.getByRole('button', { name: /^lowercase/ }))
    await user.click(screen.getByRole('button', { name: /^Apply/ }))

    const [segments] = onSave.mock.calls[0]
    expect(segments[0].words[0].start_time).toBe(0)
    expect(segments[0].words[0].end_time).toBe(1)
    expect(segments[0].words[0].id).toBe('w1')
    expect(segments[0].start_time).toBe(0)
    expect(segments[0].end_time).toBe(2)
  })

  it('applies Sentence case per-line (first word only capitalized)', async () => {
    const onSave = jest.fn()
    const user = userEvent.setup()
    render(<ReplaceAllLyricsModal {...defaultProps} onSave={onSave} />)
    await user.click(screen.getByText('Change Case'))
    await user.click(screen.getByRole('button', { name: /^Sentence case/ }))
    await user.click(screen.getByRole('button', { name: /^Apply/ }))

    const [segments, meta] = onSave.mock.calls[0]
    expect(segments[0].text).toBe('Hello world')
    expect(segments[0].words.map((w: { text: string }) => w.text)).toEqual(['Hello', 'world'])
    expect(segments[1].text).toBe('Goodbye moon')
    expect(segments[1].words.map((w: { text: string }) => w.text)).toEqual(['Goodbye', 'moon'])
    expect(meta.details).toEqual({ case_type: 'sentence' })
  })

  it('back button returns to mode selection', async () => {
    const user = await openChangeCaseMode()
    // There are two back-like buttons in the dialog header; the left-arrow is first
    const backButtons = screen.getAllByRole('button')
    // The ArrowLeft icon button is the one just before the title
    // Simpler: click the ArrowLeft (first icon button in the heading)
    const arrowBack = backButtons.find((b) => b.querySelector('svg.lucide-arrow-left'))
    expect(arrowBack).toBeDefined()
    await user.click(arrowBack!)
    expect(screen.getByText('Choose how you want to edit the lyrics:')).toBeInTheDocument()
  })
})
