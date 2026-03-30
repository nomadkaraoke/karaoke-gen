import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import GuidancePanel from '../GuidancePanel'
import { CorrectionData } from '@/lib/lyrics-review/types'

// Mock localStorage
const localStorageMock = (() => {
  let store: Record<string, string> = {}
  return {
    getItem: jest.fn((key: string) => store[key] || null),
    setItem: jest.fn((key: string, value: string) => { store[key] = value }),
    removeItem: jest.fn((key: string) => { delete store[key] }),
    clear: jest.fn(() => { store = {} }),
  }
})()
Object.defineProperty(window, 'localStorage', { value: localStorageMock })

const makeMockData = (overrides: Partial<CorrectionData> = {}): CorrectionData => ({
  corrected_segments: [],
  original_segments: [],
  anchor_sequences: [
    {
      id: 'a1',
      transcribed_word_ids: ['w1', 'w2'],
      reference_word_ids: { source1: ['r1', 'r2'] },
      confidence: 0.9,
    },
  ],
  gap_sequences: [
    {
      id: 'g1',
      transcribed_word_ids: ['w3'],
      reference_word_ids: { source1: ['r3'] },
    },
    {
      id: 'g2',
      transcribed_word_ids: ['w4', 'w5'],
      reference_word_ids: { source1: ['r4'] },
    },
  ],
  corrections: [
    {
      word_id: 'w3',
      corrected_word_id: 'w3c',
      original_word: 'orig',
      corrected_word: 'fixed',
      handler: 'TestHandler',
      confidence: 0.8,
      source: 'source1',
      reason: 'test',
      is_deletion: false,
    },
  ],
  reference_lyrics: {},
  metadata: {
    total_words: 10,
    anchor_sequences_count: 1,
    available_handlers: [],
    enabled_handlers: [],
  },
  ...overrides,
} as unknown as CorrectionData)

describe('GuidancePanel', () => {
  const defaultProps = {
    isReadOnly: false,
    onMetricClick: { anchor: jest.fn(), corrected: jest.fn(), uncorrected: jest.fn() },
    onHandlerToggle: jest.fn(),
    isUpdatingHandlers: false,
    onHandlerClick: jest.fn(),
    statsVisible: false,
    onStatsToggle: jest.fn(),
  }

  beforeEach(() => {
    localStorageMock.clear()
    jest.clearAllMocks()
  })

  it('renders color legend with all three dot labels', () => {
    render(<GuidancePanel data={makeMockData()} {...defaultProps} />)

    expect(screen.getByText('Matched')).toBeInTheDocument()
    expect(screen.getByText('Gaps (needs review)')).toBeInTheDocument()
    expect(screen.getByText('Corrected')).toBeInTheDocument()
  })

  it('renders workflow tip by default', () => {
    render(<GuidancePanel data={makeMockData()} {...defaultProps} />)

    expect(screen.getByText(/Synced Lyrics/)).toBeInTheDocument()
  })

  it('shows zero-gap message when no gaps exist and references are present', () => {
    const data = makeMockData({
      gap_sequences: [],
      corrections: [],
      reference_lyrics: { genius: { segments: [], metadata: { source: 'genius' }, source: 'genius' } },
    })
    render(<GuidancePanel data={data} {...defaultProps} />)

    expect(screen.getByText(/All lyrics matched the reference/)).toBeInTheDocument()
  })

  it('shows no-reference message when no reference sources exist', () => {
    const data = makeMockData({ gap_sequences: [], corrections: [], reference_lyrics: {} })
    render(<GuidancePanel data={data} {...defaultProps} />)

    expect(screen.getByText(/No reference lyrics were found/)).toBeInTheDocument()
  })

  it('dismisses tip and stores in localStorage', async () => {
    const user = userEvent.setup()
    render(<GuidancePanel data={makeMockData()} {...defaultProps} />)

    const dismissButton = screen.getByLabelText('Dismiss tip')
    await user.click(dismissButton)

    expect(localStorageMock.setItem).toHaveBeenCalledWith('lyricsReviewGuidanceDismissed', 'true')
    expect(screen.queryByText(/Synced Lyrics/)).not.toBeInTheDocument()
  })

  it('shows "Show tips" link after dismissal', async () => {
    const user = userEvent.setup()
    render(<GuidancePanel data={makeMockData()} {...defaultProps} />)

    await user.click(screen.getByLabelText('Dismiss tip'))

    expect(screen.getByText('Show tips')).toBeInTheDocument()
  })

  it('restores tip when "Show tips" is clicked', async () => {
    const user = userEvent.setup()
    render(<GuidancePanel data={makeMockData()} {...defaultProps} />)

    await user.click(screen.getByLabelText('Dismiss tip'))
    await user.click(screen.getByText('Show tips'))

    expect(screen.getByText(/Synced Lyrics/)).toBeInTheDocument()
    expect(localStorageMock.removeItem).toHaveBeenCalledWith('lyricsReviewGuidanceDismissed')
  })

  it('hides workflow tip in read-only mode', () => {
    render(<GuidancePanel data={makeMockData()} {...defaultProps} isReadOnly={true} />)

    expect(screen.queryByText(/Synced Lyrics/)).not.toBeInTheDocument()
  })

  it('stats are collapsed by default', () => {
    render(<GuidancePanel data={makeMockData()} {...defaultProps} />)

    expect(screen.getByText('Stats')).toBeInTheDocument()
    // Stats content should not be visible
    expect(screen.queryByText('Anchor Sequences')).not.toBeInTheDocument()
  })

  it('calls onStatsToggle when Stats button clicked', async () => {
    const user = userEvent.setup()
    render(<GuidancePanel data={makeMockData()} {...defaultProps} />)

    await user.click(screen.getByText('Stats'))

    expect(defaultProps.onStatsToggle).toHaveBeenCalled()
  })

  it('shows stats content when statsVisible is true', () => {
    render(<GuidancePanel data={makeMockData()} {...defaultProps} statsVisible={true} />)

    expect(screen.getByText('Anchor Sequences')).toBeInTheDocument()
    expect(screen.getByText('Corrected Gaps')).toBeInTheDocument()
    expect(screen.getByText('Uncorrected Gaps')).toBeInTheDocument()
  })

  it('calls metric click handler when color legend dot clicked', async () => {
    const user = userEvent.setup()
    render(<GuidancePanel data={makeMockData()} {...defaultProps} />)

    await user.click(screen.getByText('Matched'))
    expect(defaultProps.onMetricClick.anchor).toHaveBeenCalled()

    await user.click(screen.getByText('Gaps (needs review)'))
    expect(defaultProps.onMetricClick.uncorrected).toHaveBeenCalled()

    await user.click(screen.getByText('Corrected'))
    expect(defaultProps.onMetricClick.corrected).toHaveBeenCalled()
  })
})
