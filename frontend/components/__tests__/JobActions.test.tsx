/**
 * Tests for JobActions component
 */

import { render, screen } from '@testing-library/react'
import { JobActions } from '../job/JobActions'
import { Job } from '@/lib/api'

// Mock the API module
jest.mock('@/lib/api', () => ({
  api: {
    retryJob: jest.fn(),
    deleteJob: jest.fn(),
  },
  getAccessToken: jest.fn(),
}))

// Mock the auth module
jest.mock('@/lib/auth', () => ({
  useAuth: Object.assign(jest.fn(() => ({
    fetchUser: jest.fn(),
  })), {
    getState: jest.fn(() => ({ user: { credits: 5 } })),
  }),
}))

describe('JobActions', () => {
  const mockOnRefresh = jest.fn()

  const baseJob: Partial<Job> = {
    job_id: '123',
    created_at: '2025-01-01T00:00:00Z',
    updated_at: '2025-01-01T00:00:00Z',
    artist: 'Test Artist',
    title: 'Test Song',
  }

  beforeEach(() => {
    jest.clearAllMocks()
  })

  describe('Retry button visibility', () => {
    it('shows retry button for failed jobs', () => {
      const failedJob = { ...baseJob, status: 'failed' } as Job
      render(
        <JobActions
          job={failedJob}
          onRefresh={mockOnRefresh}
        />
      )

      expect(screen.getByText('Retry')).toBeInTheDocument()
    })

    it('does not show retry button for cancelled jobs (legacy status)', () => {
      const cancelledJob = { ...baseJob, status: 'cancelled' } as Job
      render(
        <JobActions
          job={cancelledJob}
          onRefresh={mockOnRefresh}
        />
      )

      // Cancelled is no longer retryable - cancel now deletes the job
      expect(screen.queryByText('Retry')).not.toBeInTheDocument()
    })

    it('does not show retry button for pending jobs', () => {
      const pendingJob = { ...baseJob, status: 'pending' } as Job
      render(
        <JobActions
          job={pendingJob}
          onRefresh={mockOnRefresh}
        />
      )

      expect(screen.queryByText('Retry')).not.toBeInTheDocument()
    })

    it('does not show retry button for processing jobs', () => {
      const processingJob = { ...baseJob, status: 'processing' } as Job
      render(
        <JobActions
          job={processingJob}
          onRefresh={mockOnRefresh}
        />
      )

      expect(screen.queryByText('Retry')).not.toBeInTheDocument()
    })

    it('does not show retry button for complete jobs', () => {
      const completeJob = { ...baseJob, status: 'complete' } as Job
      render(
        <JobActions
          job={completeJob}
          onRefresh={mockOnRefresh}
        />
      )

      expect(screen.queryByText('Retry')).not.toBeInTheDocument()
    })
  })

  describe('Delete button visibility', () => {
    it('shows delete button for pending jobs', () => {
      const pendingJob = { ...baseJob, status: 'pending' } as Job
      render(
        <JobActions
          job={pendingJob}
          onRefresh={mockOnRefresh}
        />
      )

      expect(screen.getByText('Delete')).toBeInTheDocument()
    })

    it('does not show delete button for failed jobs', () => {
      const failedJob = { ...baseJob, status: 'failed' } as Job
      render(
        <JobActions
          job={failedJob}
          onRefresh={mockOnRefresh}
        />
      )

      expect(screen.queryByText('Delete')).not.toBeInTheDocument()
    })

    it('does not show delete button for complete jobs', () => {
      const completeJob = { ...baseJob, status: 'complete' } as Job
      render(
        <JobActions
          job={completeJob}
          onRefresh={mockOnRefresh}
        />
      )

      expect(screen.queryByText('Delete')).not.toBeInTheDocument()
    })
  })

  describe('Renders nothing when no actions available', () => {
    it('returns null for complete jobs (no delete, no retry)', () => {
      const completeJob = { ...baseJob, status: 'complete' } as Job
      const { container } = render(
        <JobActions
          job={completeJob}
          onRefresh={mockOnRefresh}
        />
      )

      // Component should render nothing when no actions are available
      expect(container.firstChild).toBeNull()
    })
  })
})
