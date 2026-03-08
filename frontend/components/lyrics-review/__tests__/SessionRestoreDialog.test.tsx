import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import SessionRestoreDialog from '../SessionRestoreDialog'
import type { ReviewSession, LyricsReviewApiClient, ReviewSessionWithData } from '@/lib/api'

function makeSession(overrides: Partial<ReviewSession> = {}): ReviewSession {
  return {
    session_id: 'session-1',
    job_id: 'job-1',
    user_email: 'user@test.com',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    edit_count: 5,
    trigger: 'auto',
    audio_duration_seconds: 180,
    artist: 'Test Artist',
    title: 'Test Song',
    summary: {
      total_segments: 10,
      total_words: 100,
      corrections_made: 3,
      changed_words: [
        { original: 'hello', corrected: 'hallo', segment_index: 0 },
        { original: 'world', corrected: 'werld', segment_index: 1 },
      ],
    },
    ...overrides,
  }
}

function makeMockApiClient(): LyricsReviewApiClient {
  return {
    submitCorrections: jest.fn(),
    submitAnnotations: jest.fn(),
    submitEditLog: jest.fn(),
    updateHandlers: jest.fn(),
    addLyrics: jest.fn(),
    getAudioUrl: jest.fn(),
    generatePreviewVideo: jest.fn(),
    getPreviewVideoUrl: jest.fn(),
    completeReview: jest.fn(),
    saveReviewSession: jest.fn(),
    listReviewSessions: jest.fn(),
    getReviewSession: jest.fn().mockResolvedValue({
      session_id: 'session-1',
      correction_data: { corrected_segments: [] },
    } as Partial<ReviewSessionWithData>),
    deleteReviewSession: jest.fn(),
  }
}

describe('SessionRestoreDialog', () => {
  const defaultProps = {
    open: true,
    onClose: jest.fn(),
    onRestore: jest.fn(),
    sessions: [makeSession()],
    apiClient: makeMockApiClient(),
    currentAudioDuration: 180,
  }

  it('renders dialog with title when open', () => {
    render(<SessionRestoreDialog {...defaultProps} />)
    expect(screen.getByText('Saved Review Sessions')).toBeInTheDocument()
  })

  it('does not render content when closed', () => {
    render(<SessionRestoreDialog {...defaultProps} open={false} />)
    expect(screen.queryByText('Saved Review Sessions')).not.toBeInTheDocument()
  })

  it('shows loading spinner when isLoading', () => {
    render(<SessionRestoreDialog {...defaultProps} isLoading={true} />)
    // Should not show session list
    expect(screen.queryByText('5 edits')).not.toBeInTheDocument()
  })

  it('shows empty state when no sessions', () => {
    render(<SessionRestoreDialog {...defaultProps} sessions={[]} />)
    expect(screen.getByText('No saved sessions found')).toBeInTheDocument()
  })

  it('displays session list with edit count', () => {
    render(<SessionRestoreDialog {...defaultProps} />)
    expect(screen.getByText('5 edits')).toBeInTheDocument()
  })

  it('shows trigger badge', () => {
    render(<SessionRestoreDialog {...defaultProps} />)
    expect(screen.getByText('auto')).toBeInTheDocument()
  })

  it('shows edit preview for selected session', () => {
    render(<SessionRestoreDialog {...defaultProps} />)
    // First session should be auto-selected
    expect(screen.getByText('hello')).toBeInTheDocument()
    expect(screen.getByText('hallo')).toBeInTheDocument()
    expect(screen.getByText('world')).toBeInTheDocument()
    expect(screen.getByText('werld')).toBeInTheDocument()
  })

  it('shows correction count in preview', () => {
    render(<SessionRestoreDialog {...defaultProps} />)
    expect(screen.getByText(/3 changes? across 10 segments/)).toBeInTheDocument()
  })

  it('shows "No word-level changes" when summary has no changed_words', () => {
    const session = makeSession({
      summary: { total_segments: 5, total_words: 50, corrections_made: 0, changed_words: [] },
    })
    render(<SessionRestoreDialog {...defaultProps} sessions={[session]} />)
    expect(screen.getByText('No word-level changes recorded in this session.')).toBeInTheDocument()
  })

  it('renders multiple sessions in the list', () => {
    const sessions = [
      makeSession({ session_id: 's1', edit_count: 3, trigger: 'manual' }),
      makeSession({ session_id: 's2', edit_count: 10, trigger: 'preview' }),
    ]
    render(<SessionRestoreDialog {...defaultProps} sessions={sessions} />)
    expect(screen.getByText('3 edits')).toBeInTheDocument()
    expect(screen.getByText('10 edits')).toBeInTheDocument()
    expect(screen.getByText('manual')).toBeInTheDocument()
    expect(screen.getByText('preview')).toBeInTheDocument()
  })

  it('calls onRestore with correction data when Restore Selected is clicked', async () => {
    const user = userEvent.setup()
    const apiClient = makeMockApiClient()
    const onRestore = jest.fn()
    const correctionData = { corrected_segments: [{ id: 's1', text: 'test', words: [], start_time: 0, end_time: 1 }] }

    ;(apiClient.getReviewSession as jest.Mock).mockResolvedValue({
      session_id: 'session-1',
      correction_data: correctionData,
    })

    render(
      <SessionRestoreDialog
        {...defaultProps}
        apiClient={apiClient}
        onRestore={onRestore}
      />
    )

    await user.click(screen.getByText('Restore Selected'))

    expect(apiClient.getReviewSession).toHaveBeenCalledWith('session-1')
    expect(onRestore).toHaveBeenCalledWith(correctionData)
  })

  it('calls onClose when Start Fresh is clicked', async () => {
    const user = userEvent.setup()
    const onClose = jest.fn()
    render(<SessionRestoreDialog {...defaultProps} onClose={onClose} />)

    await user.click(screen.getByText('Start Fresh'))
    expect(onClose).toHaveBeenCalled()
  })

  it('shows tab buttons for This Job and All Jobs', () => {
    render(<SessionRestoreDialog {...defaultProps} />)
    expect(screen.getByText(/This Job/)).toBeInTheDocument()
    expect(screen.getByText('All Jobs')).toBeInTheDocument()
  })

  it('shows session count in This Job tab', () => {
    const sessions = [makeSession({ session_id: 's1' }), makeSession({ session_id: 's2' })]
    render(<SessionRestoreDialog {...defaultProps} sessions={sessions} />)
    expect(screen.getByText('This Job (2)')).toBeInTheDocument()
  })

  it('shows search input when All Jobs tab is selected', async () => {
    const user = userEvent.setup()
    render(<SessionRestoreDialog {...defaultProps} />)

    await user.click(screen.getByText('All Jobs'))
    expect(screen.getByPlaceholderText('Search by artist, title, or job ID...')).toBeInTheDocument()
  })

  it('shows artist and title for cross-job sessions', async () => {
    const user = userEvent.setup()

    // Mock the global fetch for cross-job search
    const mockFetch = jest.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        sessions: [
          makeSession({ session_id: 'cross-1', artist: 'Other Artist', title: 'Other Song', edit_count: 7 }),
        ],
      }),
    })
    global.fetch = mockFetch

    render(<SessionRestoreDialog {...defaultProps} />)

    // Switch to All Jobs tab
    await user.click(screen.getByText('All Jobs'))

    // Type search query and submit
    const input = screen.getByPlaceholderText('Search by artist, title, or job ID...')
    await user.type(input, 'Other Artist{Enter}')

    // Wait for results
    expect(mockFetch).toHaveBeenCalled()

    // Should show the cross-job session with artist info
    expect(await screen.findByText(/Other Artist/)).toBeInTheDocument()
  })
})
