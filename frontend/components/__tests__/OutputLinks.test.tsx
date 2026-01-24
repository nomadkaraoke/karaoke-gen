/**
 * Tests for OutputLinks component
 *
 * Tests the download buttons and external links behavior,
 * including hiding links when outputs have been deleted.
 */

import { render, screen } from '@testing-library/react'
import { OutputLinks } from '../job/OutputLinks'
import { Job } from '@/lib/api'

// Mock the hooks
jest.mock('@/lib/auth', () => ({
  useAuth: jest.fn(() => ({ user: { role: 'user' } })),
}))

jest.mock('@/lib/tenant', () => ({
  useTenant: jest.fn(() => ({
    features: {
      youtube_upload: true,
      dropbox_upload: true,
    },
  })),
}))

// Mock the api module
jest.mock('@/lib/api', () => ({
  api: {
    getDownloadUrl: jest.fn((jobId, category, key) => `https://example.com/${jobId}/${category}/${key}`),
  },
  adminApi: {
    getCompletionMessage: jest.fn(),
    sendCompletionEmail: jest.fn(),
  },
}))

describe('OutputLinks', () => {
  const baseJob: Job = {
    job_id: 'test-123',
    status: 'complete',
    progress: 100,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    artist: 'Test Artist',
    title: 'Test Song',
    file_urls: {
      finals: {
        lossy_4k_mp4: 'gs://bucket/finals/4k.mp4',
        lossy_720p_mp4: 'gs://bucket/finals/720p.mp4',
      },
      videos: {
        with_vocals: 'gs://bucket/videos/with_vocals.mkv',
      },
      packages: {
        cdg_zip: 'gs://bucket/packages/cdg.zip',
        txt_zip: 'gs://bucket/packages/txt.zip',
      },
    },
    state_data: {
      youtube_url: 'https://youtube.com/watch?v=abc123',
      dropbox_link: 'https://dropbox.com/s/xyz789',
    },
  }

  beforeEach(() => {
    jest.clearAllMocks()
    // Reset to default non-admin user
    const { useAuth } = require('@/lib/auth')
    useAuth.mockReturnValue({ user: { role: 'user' } })
  })

  describe('when outputs are NOT deleted', () => {
    it('shows download buttons when file_urls are present', () => {
      render(<OutputLinks job={baseJob} />)

      expect(screen.getByText('4K Video')).toBeInTheDocument()
      expect(screen.getByText('720p Video')).toBeInTheDocument()
      expect(screen.getByText('With Vocals')).toBeInTheDocument()
      expect(screen.getByText('CDG')).toBeInTheDocument()
      expect(screen.getByText('TXT')).toBeInTheDocument()
    })

    it('shows YouTube link when youtube_url is in state_data', () => {
      render(<OutputLinks job={baseJob} />)

      expect(screen.getByText('YouTube')).toBeInTheDocument()
    })

    it('shows Dropbox link when dropbox_link is in state_data', () => {
      render(<OutputLinks job={baseJob} />)

      expect(screen.getByText('Dropbox')).toBeInTheDocument()
    })
  })

  describe('when outputs ARE deleted (outputs_deleted_at is set)', () => {
    const deletedJob: Job = {
      ...baseJob,
      outputs_deleted_at: '2026-01-15T10:00:00Z',
      outputs_deleted_by: 'admin@example.com',
    }

    it('hides all download buttons', () => {
      render(<OutputLinks job={deletedJob} />)

      expect(screen.queryByText('4K Video')).not.toBeInTheDocument()
      expect(screen.queryByText('720p Video')).not.toBeInTheDocument()
      expect(screen.queryByText('With Vocals')).not.toBeInTheDocument()
      expect(screen.queryByText('CDG')).not.toBeInTheDocument()
      expect(screen.queryByText('TXT')).not.toBeInTheDocument()
    })

    it('hides YouTube link', () => {
      render(<OutputLinks job={deletedJob} />)

      expect(screen.queryByText('YouTube')).not.toBeInTheDocument()
    })

    it('hides Dropbox link', () => {
      render(<OutputLinks job={deletedJob} />)

      expect(screen.queryByText('Dropbox')).not.toBeInTheDocument()
    })

    it('still shows Admin link for admin users', () => {
      // Mock admin user
      const { useAuth } = require('@/lib/auth')
      useAuth.mockReturnValue({ user: { role: 'admin' } })

      render(<OutputLinks job={deletedJob} />)

      // Admin link should still be visible even when outputs are deleted
      expect(screen.getByText('Admin')).toBeInTheDocument()
    })
  })

  describe('edge cases', () => {
    it('shows nothing when job has no file_urls and no state_data (non-admin)', () => {
      // Reset to non-admin user
      const { useAuth } = require('@/lib/auth')
      useAuth.mockReturnValue({ user: { role: 'user' } })

      const emptyJob: Job = {
        ...baseJob,
        file_urls: undefined,
        state_data: undefined,
      }

      render(<OutputLinks job={emptyJob} />)

      // Should show "No outputs available yet" for non-admin
      expect(screen.getByText('No outputs available yet')).toBeInTheDocument()
    })

    it('handles partial file_urls gracefully', () => {
      // Reset to non-admin user
      const { useAuth } = require('@/lib/auth')
      useAuth.mockReturnValue({ user: { role: 'user' } })

      const partialJob: Job = {
        ...baseJob,
        file_urls: {
          finals: {
            lossy_720p_mp4: 'gs://bucket/finals/720p.mp4',
          },
        },
        state_data: undefined,
      }

      render(<OutputLinks job={partialJob} />)

      expect(screen.getByText('720p Video')).toBeInTheDocument()
      expect(screen.queryByText('4K Video')).not.toBeInTheDocument()
      expect(screen.queryByText('YouTube')).not.toBeInTheDocument()
    })
  })
})
