/**
 * Tests for JobCard component
 *
 * Note: Job cards are no longer collapsible - details are always visible
 */

import { render, screen } from '@testing-library/react'
import { JobCard } from '../job/JobCard'
import { Job } from '@/lib/api'

// Mock the child components
jest.mock('../job/JobActions', () => ({
  JobActions: () => <div data-testid="job-actions">Job Actions</div>
}))

jest.mock('../job/AdminJobActions', () => ({
  AdminJobActions: () => <div data-testid="admin-job-actions">Admin Job Actions</div>
}))

jest.mock('../job/JobLogs', () => ({
  JobLogs: () => <div data-testid="job-logs">Job Logs</div>
}))

jest.mock('../job/OutputLinks', () => ({
  OutputLinks: () => <div data-testid="output-links">Output Links</div>
}))

jest.mock('../audio-search/AudioSearchDialog', () => ({
  AudioSearchDialog: () => <div data-testid="audio-search-dialog">Audio Search Dialog</div>
}))

describe('JobCard', () => {
  const mockOnRefresh = jest.fn()

  const mockJob: Job = {
    job_id: '123',
    status: 'processing',
    progress: 50,
    created_at: '2025-01-01T00:00:00Z',
    updated_at: '2025-01-01T00:00:00Z',
    artist: 'Test Artist',
    title: 'Test Song',
  }

  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('renders job information', () => {
    render(<JobCard job={mockJob} onRefresh={mockOnRefresh} />)

    expect(screen.getByText('Test Artist - Test Song')).toBeInTheDocument()
  })

  it('shows job ID (details always visible)', () => {
    render(<JobCard job={mockJob} onRefresh={mockOnRefresh} />)

    // Details are always visible now (no need to click to expand)
    // ID is shown in format: "ID: 123 • date • status"
    expect(screen.getByText(/ID:/)).toBeInTheDocument()
    expect(screen.getByText(/123/)).toBeInTheDocument()
  })

  it('shows job actions (details always visible)', () => {
    render(<JobCard job={mockJob} onRefresh={mockOnRefresh} />)

    // Actions should be immediately visible
    expect(screen.getByTestId('job-actions')).toBeInTheDocument()
  })

  // Note: Progress bars have been removed from JobCard in favor of a simpler design

  it('shows error message for failed jobs', () => {
    const failedJob = { ...mockJob, status: 'failed', error_message: 'Something went wrong' }
    render(<JobCard job={failedJob} onRefresh={mockOnRefresh} />)

    expect(screen.getByText('Something went wrong')).toBeInTheDocument()
  })

  // Note: Orange border for interactive jobs removed - uses default slate border now

  it('applies green border for complete jobs', () => {
    const completeJob = { ...mockJob, status: 'complete' }
    const { container } = render(<JobCard job={completeJob} onRefresh={mockOnRefresh} />)

    const card = container.firstChild
    expect(card).toHaveClass('border-green-500/30')
  })

  it('applies red border for failed jobs', () => {
    const failedJob = { ...mockJob, status: 'failed' }
    const { container } = render(<JobCard job={failedJob} onRefresh={mockOnRefresh} />)

    const card = container.firstChild
    expect(card).toHaveClass('border-red-500/30')
  })

  describe('regular jobs (non_interactive: false)', () => {
    it('shows lyrics review link for awaiting_review status', () => {
      const reviewJob = { ...mockJob, status: 'awaiting_review', review_token: 'test-token-123' }
      render(<JobCard job={reviewJob} onRefresh={mockOnRefresh} />)

      const reviewLink = screen.getByText('Review Lyrics')
      expect(reviewLink).toBeInTheDocument()
      const href = reviewLink.closest('a')?.getAttribute('href') || ''
      // Uses hash-based routing for static hosting
      // Uses <a> tag (not Next.js Link) to ensure hashchange event fires
      expect(href).toBe('/app/jobs#/123/review')
    })

    it('shows audio search button for awaiting_audio_selection status', () => {
      const audioSelectionJob = { ...mockJob, status: 'awaiting_audio_selection' }
      render(<JobCard job={audioSelectionJob} onRefresh={mockOnRefresh} />)

      expect(screen.getByRole('button', { name: 'Select Audio' })).toBeInTheDocument()
    })

    it('shows instrumental selection link for awaiting_instrumental_selection status', () => {
      const instrumentalJob = {
        ...mockJob,
        status: 'awaiting_instrumental_selection',
        instrumental_token: 'test-instrumental-token'
      }
      render(<JobCard job={instrumentalJob} onRefresh={mockOnRefresh} />)

      const instrumentalLink = screen.getByText('Select Instrumental')
      expect(instrumentalLink).toBeInTheDocument()
      const href = instrumentalLink.closest('a')?.getAttribute('href') || ''
      // Uses hash-based routing for static hosting
      // Uses <a> tag (not Next.js Link) to ensure hashchange event fires
      expect(href).toBe('/app/jobs#/123/instrumental')
    })

    it('does not show Auto badge for regular jobs', () => {
      render(<JobCard job={mockJob} onRefresh={mockOnRefresh} />)

      expect(screen.queryByText('Auto')).not.toBeInTheDocument()
    })
  })

  describe('non-interactive jobs (non_interactive: true)', () => {
    it('shows Auto badge for non-interactive jobs', () => {
      const nonInteractiveJob = { ...mockJob, non_interactive: true }
      render(<JobCard job={nonInteractiveJob} onRefresh={mockOnRefresh} />)

      expect(screen.getByText('Auto')).toBeInTheDocument()
    })

    it('shows auto-accept message for awaiting_review status', () => {
      const reviewJob = { ...mockJob, status: 'awaiting_review', non_interactive: true }
      render(<JobCard job={reviewJob} onRefresh={mockOnRefresh} />)

      expect(screen.getByText(/Will auto-accept/)).toBeInTheDocument()
      expect(screen.queryByText('Review Lyrics')).not.toBeInTheDocument()
    })

    it('shows auto-select message for awaiting_instrumental_selection status', () => {
      const instrumentalJob = { ...mockJob, status: 'awaiting_instrumental_selection', non_interactive: true }
      render(<JobCard job={instrumentalJob} onRefresh={mockOnRefresh} />)

      expect(screen.getByText(/Will auto-select/)).toBeInTheDocument()
      expect(screen.queryByText('Select Instrumental')).not.toBeInTheDocument()
    })

    it('shows auto-select first message for awaiting_audio_selection status', () => {
      const audioJob = { ...mockJob, status: 'awaiting_audio_selection', non_interactive: true }
      render(<JobCard job={audioJob} onRefresh={mockOnRefresh} />)

      expect(screen.getByText(/Will auto-select first/)).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Select Audio' })).not.toBeInTheDocument()
    })
  })

  it('shows output links for complete jobs', () => {
    const completeJob = { ...mockJob, status: 'complete' }
    render(<JobCard job={completeJob} onRefresh={mockOnRefresh} />)

    expect(screen.getByTestId('output-links')).toBeInTheDocument()
  })

  it('handles unknown artist gracefully', () => {
    const noArtistJob = { ...mockJob, artist: undefined }
    render(<JobCard job={noArtistJob} onRefresh={mockOnRefresh} />)

    expect(screen.getByText('Unknown - Test Song')).toBeInTheDocument()
  })

  it('handles unknown title gracefully', () => {
    const noTitleJob = { ...mockJob, title: undefined }
    render(<JobCard job={noTitleJob} onRefresh={mockOnRefresh} />)

    expect(screen.getByText('Test Artist - Unknown')).toBeInTheDocument()
  })

  describe('admin controls', () => {
    it('shows admin job actions when showAdminControls is true', () => {
      render(<JobCard job={mockJob} onRefresh={mockOnRefresh} showAdminControls={true} />)

      expect(screen.getByTestId('admin-job-actions')).toBeInTheDocument()
    })

    it('does not show admin job actions when showAdminControls is false', () => {
      render(<JobCard job={mockJob} onRefresh={mockOnRefresh} showAdminControls={false} />)

      expect(screen.queryByTestId('admin-job-actions')).not.toBeInTheDocument()
    })

    it('does not show admin job actions by default', () => {
      render(<JobCard job={mockJob} onRefresh={mockOnRefresh} />)

      expect(screen.queryByTestId('admin-job-actions')).not.toBeInTheDocument()
    })
  })
})
