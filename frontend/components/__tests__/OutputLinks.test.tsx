/**
 * Tests for OutputLinks component
 *
 * Tests the download buttons and external links behavior,
 * including hiding links when outputs have been deleted.
 */

import { render, screen, fireEvent, waitFor } from '@testing-library/react'
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

const mockToast = jest.fn()
jest.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: mockToast }),
}))

// Mock the api module
jest.mock('@/lib/api', () => ({
  api: {
    getDownloadUrl: jest.fn((jobId, category, key) => `https://example.com/${jobId}/${category}/${key}`),
    rerenderWithCurrentTheme: jest.fn(() => Promise.resolve({ status: 'processing' })),
    regenerateJob: jest.fn(() => Promise.resolve({ status: 'processing', needs_stems: true })),
  },
  adminApi: {
    getCompletionMessage: jest.fn(),
    sendCompletionEmail: jest.fn(),
    rerenderJob: jest.fn(() => Promise.resolve({ status: 'processing' })),
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
        portrait_1080x1920: 'gs://bucket/finals/portrait_1080x1920.mp4',
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
      expect(screen.getByText('Portrait Video')).toBeInTheDocument()
      expect(screen.getByText('With Vocals')).toBeInTheDocument()
      expect(screen.getByText('CDG')).toBeInTheDocument()
      expect(screen.getByText('TXT')).toBeInTheDocument()
    })

    it('hides the Portrait Video button when the job has no portrait output', () => {
      const noPortrait = {
        ...baseJob,
        file_urls: {
          ...baseJob.file_urls,
          finals: {
            lossy_4k_mp4: 'gs://bucket/finals/4k.mp4',
            lossy_720p_mp4: 'gs://bucket/finals/720p.mp4',
          },
        },
      }
      render(<OutputLinks job={noPortrait} />)

      expect(screen.getByText('720p Video')).toBeInTheDocument()
      expect(screen.queryByText('Portrait Video')).not.toBeInTheDocument()
    })

    it('shows YouTube link when youtube_url is in state_data', () => {
      render(<OutputLinks job={baseJob} />)

      expect(screen.getByText('YouTube')).toBeInTheDocument()
    })

    it('hides GCS downloads + distribution links during a private->public re-render', () => {
      // Making public deletes the finals and re-renders (status leaves
      // "complete"), so the now-stale download + distribution links must go.
      const reRenderJob: Job = {
        ...baseJob,
        status: 'rendering_video',
        state_data: {
          ...baseJob.state_data,
          visibility_change_in_progress: true,
        },
      }

      render(<OutputLinks job={reRenderJob} />)

      expect(screen.queryByText('4K Video')).not.toBeInTheDocument()
      expect(screen.queryByText('720p Video')).not.toBeInTheDocument()
      expect(screen.queryByText('YouTube')).not.toBeInTheDocument()
      expect(screen.queryByText('Dropbox')).not.toBeInTheDocument()
    })

    it('keeps GCS downloads but hides distribution links during a public->private change', () => {
      // Making private is the fast path: it keeps the GCS finals and stays
      // "complete", only tearing down + redistributing YouTube/Dropbox.
      const toPrivateJob: Job = {
        ...baseJob,
        status: 'complete',
        state_data: {
          ...baseJob.state_data,
          visibility_change_in_progress: true,
        },
      }

      render(<OutputLinks job={toPrivateJob} />)

      // Finals still available for download
      expect(screen.getByText('4K Video')).toBeInTheDocument()
      expect(screen.getByText('720p Video')).toBeInTheDocument()
      // Distribution links torn down / redistributing
      expect(screen.queryByText('YouTube')).not.toBeInTheDocument()
      expect(screen.queryByText('Dropbox')).not.toBeInTheDocument()
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

  describe('visibility change button', () => {
    it('shows "Make Public" for private complete jobs', () => {
      const privateJob: Job = {
        ...baseJob,
        is_private: true,
      }

      render(<OutputLinks job={privateJob} />)

      expect(screen.getByText('Make Public')).toBeInTheDocument()
      expect(screen.queryByText('Make Private')).not.toBeInTheDocument()
    })

    it('shows "Make Private" for public complete jobs', () => {
      const publicJob: Job = {
        ...baseJob,
        is_private: false,
      }

      render(<OutputLinks job={publicJob} />)

      expect(screen.getByText('Make Private')).toBeInTheDocument()
      expect(screen.queryByText('Make Public')).not.toBeInTheDocument()
    })

    it('hides button when visibility change is in progress', () => {
      const inProgressJob: Job = {
        ...baseJob,
        state_data: {
          ...baseJob.state_data,
          visibility_change_in_progress: true,
        },
      }

      render(<OutputLinks job={inProgressJob} />)

      expect(screen.queryByText('Make Public')).not.toBeInTheDocument()
      expect(screen.queryByText('Make Private')).not.toBeInTheDocument()
    })

    it('hides button for non-complete jobs', () => {
      const pendingJob: Job = {
        ...baseJob,
        status: 'generating_video',
      }

      render(<OutputLinks job={pendingJob} />)

      expect(screen.queryByText('Make Public')).not.toBeInTheDocument()
      expect(screen.queryByText('Make Private')).not.toBeInTheDocument()
    })

    it('hides button for tenant jobs', () => {
      const { useTenant } = require('@/lib/tenant')
      useTenant.mockReturnValue({
        tenantId: 'vocalstar',
        features: { youtube_upload: true, dropbox_upload: true },
      })

      render(<OutputLinks job={baseJob} />)

      expect(screen.queryByText('Make Public')).not.toBeInTheDocument()
      expect(screen.queryByText('Make Private')).not.toBeInTheDocument()
    })
  })

  describe('edge cases', () => {
    it('shows nothing when job has no file_urls and no state_data (non-admin)', () => {
      // Reset to non-admin user
      const { useAuth } = require('@/lib/auth')
      useAuth.mockReturnValue({ user: { role: 'user' } })

      const emptyJob: Job = {
        ...baseJob,
        status: 'generating_video',
        file_urls: undefined,
        state_data: undefined,
      }

      render(<OutputLinks job={emptyJob} />)

      // Should show "No outputs available yet" for non-admin, non-complete job
      expect(screen.getByText('No outputs available yet')).toBeInTheDocument()
    })

    it('shows Edit button for completed jobs with outputs', () => {
      render(<OutputLinks job={baseJob} />)

      expect(screen.getByText('Edit')).toBeInTheDocument()
    })

    it('hides Edit button when outputs are deleted', () => {
      const deletedJob: Job = {
        ...baseJob,
        outputs_deleted_at: '2026-01-15T10:00:00Z',
      }

      render(<OutputLinks job={deletedJob} />)

      expect(screen.queryByText('Edit')).not.toBeInTheDocument()
    })

    it('hides Edit button for non-complete jobs', () => {
      const pendingJob: Job = {
        ...baseJob,
        status: 'pending',
      }

      render(<OutputLinks job={pendingJob} />)

      expect(screen.queryByText('Edit')).not.toBeInTheDocument()
    })
  })

  describe('With Vocals MP4 preference', () => {
    it('prefers finals.with_vocals_mp4 over videos.with_vocals when both exist', () => {
      const jobWithBoth: Job = {
        ...baseJob,
        file_urls: {
          finals: {
            lossy_4k_mp4: 'gs://bucket/finals/4k.mp4',
            with_vocals_mp4: 'gs://bucket/finals/with_vocals.mp4',
          },
          videos: {
            with_vocals: 'gs://bucket/videos/with_vocals.mkv',
          },
        },
      }

      render(<OutputLinks job={jobWithBoth} />)

      const vocalsLink = screen.getByText('With Vocals').closest('a')
      expect(vocalsLink).toHaveAttribute('href',
        expect.stringContaining('/finals/with_vocals_mp4')
      )
    })

    it('falls back to videos.with_vocals when finals.with_vocals_mp4 is missing', () => {
      render(<OutputLinks job={baseJob} />)

      const vocalsLink = screen.getByText('With Vocals').closest('a')
      expect(vocalsLink).toHaveAttribute('href',
        expect.stringContaining('/videos/with_vocals')
      )
    })

    it('shows With Vocals when only finals.with_vocals_mp4 exists (no legacy MKV)', () => {
      const mp4OnlyJob: Job = {
        ...baseJob,
        file_urls: {
          finals: {
            lossy_4k_mp4: 'gs://bucket/finals/4k.mp4',
            with_vocals_mp4: 'gs://bucket/finals/with_vocals.mp4',
          },
        },
      }

      render(<OutputLinks job={mp4OnlyJob} />)

      expect(screen.getByText('With Vocals')).toBeInTheDocument()
      const vocalsLink = screen.getByText('With Vocals').closest('a')
      expect(vocalsLink).toHaveAttribute('href',
        expect.stringContaining('/finals/with_vocals_mp4')
      )
    })
  })

  describe('edge cases', () => {
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

  describe('re-render with current theme (tenant portals)', () => {
    const tenantJob: Job = { ...baseJob, state_data: {} }

    const asTenant = (tenantId: string | null) => {
      const { useTenant } = require('@/lib/tenant')
      useTenant.mockReturnValue({ tenantId, features: { youtube_upload: false, dropbox_upload: false } })
    }

    it('shows Re-render for a finished portal track', () => {
      asTenant('randy-vild')
      render(<OutputLinks job={tenantJob} />)
      expect(screen.getByText('Re-render')).toBeInTheDocument()
    })

    it('is hidden outside tenant portals', () => {
      asTenant(null)
      render(<OutputLinks job={tenantJob} />)
      expect(screen.queryByText('Re-render')).not.toBeInTheDocument()
    })

    it('is hidden while the track is not finished', () => {
      asTenant('randy-vild')
      render(<OutputLinks job={{ ...tenantJob, status: 'rendering_video' }} />)
      expect(screen.queryByText('Re-render')).not.toBeInTheDocument()
    })

    it('is hidden when outputs were deleted', () => {
      asTenant('randy-vild')
      render(<OutputLinks job={{ ...tenantJob, outputs_deleted_at: '2026-09-29T18:47:13Z' }} />)
      expect(screen.queryByText('Re-render')).not.toBeInTheDocument()
    })

    it('confirms, then starts the re-render and refreshes the job', async () => {
      asTenant('randy-vild')
      const { api } = require('@/lib/api')
      const onJobUpdated = jest.fn()
      render(<OutputLinks job={tenantJob} onJobUpdated={onJobUpdated} />)

      fireEvent.click(screen.getByText('Re-render'))
      expect(screen.getByText('Re-render with your current theme?')).toBeInTheDocument()
      expect(api.rerenderWithCurrentTheme).not.toHaveBeenCalled()

      const buttons = screen.getAllByRole('button', { name: 'Re-render' })
      fireEvent.click(buttons[buttons.length - 1])

      await waitFor(() => expect(api.rerenderWithCurrentTheme).toHaveBeenCalledWith('test-123'))
      await waitFor(() => expect(onJobUpdated).toHaveBeenCalled())
    })
  })

  describe('admin re-render (any completed job)', () => {
    const asAdmin = (isAdmin: boolean) => {
      const { useAuth } = require('@/lib/auth')
      useAuth.mockReturnValue({ user: { role: isAdmin ? 'admin' : 'user' } })
      const { useTenant } = require('@/lib/tenant')
      useTenant.mockReturnValue({ tenantId: null, features: { youtube_upload: true, dropbox_upload: true } })
    }
    const publishedJob: Job = {
      ...baseJob,
      state_data: { ...baseJob.state_data, brand_code: 'NOMAD-1234' },
    }

    it('shows Re-render to admins on a completed job', () => {
      asAdmin(true)
      render(<OutputLinks job={publishedJob} />)
      expect(screen.getByTestId('admin-rerender-button')).toHaveTextContent('Re-render')
    })

    it('is hidden from non-admins', () => {
      asAdmin(false)
      render(<OutputLinks job={publishedJob} />)
      expect(screen.queryByTestId('admin-rerender-button')).not.toBeInTheDocument()
    })

    it.each(['rendering_video', 'awaiting_review', 'failed', 'encoding'])(
      'is hidden while the job is %s',
      (status) => {
        asAdmin(true)
        render(<OutputLinks job={{ ...publishedJob, status }} />)
        expect(screen.queryByTestId('admin-rerender-button')).not.toBeInTheDocument()
      },
    )

    it('is hidden when outputs were deleted', () => {
      asAdmin(true)
      render(<OutputLinks job={{ ...publishedJob, outputs_deleted_at: '2026-09-29T18:47:13Z' }} />)
      expect(screen.queryByTestId('admin-rerender-button')).not.toBeInTheDocument()
    })

    it('is hidden during a visibility change', () => {
      asAdmin(true)
      const job: Job = { ...publishedJob, state_data: { ...publishedJob.state_data, visibility_change_in_progress: true } }
      render(<OutputLinks job={job} />)
      expect(screen.queryByTestId('admin-rerender-button')).not.toBeInTheDocument()
    })

    it('confirm dialog explains the consequences and defaults to not emailing', async () => {
      asAdmin(true)
      const { adminApi } = require('@/lib/api')
      const onJobUpdated = jest.fn()
      render(<OutputLinks job={publishedJob} onJobUpdated={onJobUpdated} />)

      fireEvent.click(screen.getByTestId('admin-rerender-button'))
      expect(screen.getByText('Re-render this track?')).toBeInTheDocument()
      expect(screen.getByText(/without review|Nothing goes back to review/)).toBeInTheDocument()
      expect(screen.getByText(/gets a NEW URL/)).toBeInTheDocument()
      expect(screen.getByText('The brand code NOMAD-1234 stays the same.')).toBeInTheDocument()
      expect(screen.getByText('Email the customer when done')).toBeInTheDocument()
      expect(screen.getByTestId('admin-rerender-notify')).toHaveAttribute('data-state', 'unchecked')
      expect(adminApi.rerenderJob).not.toHaveBeenCalled()

      fireEvent.click(screen.getByTestId('admin-rerender-confirm'))

      await waitFor(() => expect(adminApi.rerenderJob).toHaveBeenCalledWith('test-123', false))
      await waitFor(() => expect(onJobUpdated).toHaveBeenCalled())
    })

    it('sends notify_customer=true when the checkbox is ticked', async () => {
      asAdmin(true)
      const { adminApi } = require('@/lib/api')
      render(<OutputLinks job={publishedJob} />)

      fireEvent.click(screen.getByTestId('admin-rerender-button'))
      fireEvent.click(screen.getByTestId('admin-rerender-notify'))
      expect(screen.getByTestId('admin-rerender-notify')).toHaveAttribute('data-state', 'checked')
      fireEvent.click(screen.getByTestId('admin-rerender-confirm'))

      await waitFor(() => expect(adminApi.rerenderJob).toHaveBeenCalledWith('test-123', true))
    })

    it('omits the brand code line when the job has none', () => {
      asAdmin(true)
      render(<OutputLinks job={{ ...baseJob, state_data: {} }} />)
      fireEvent.click(screen.getByTestId('admin-rerender-button'))
      expect(screen.queryByText(/The brand code/)).not.toBeInTheDocument()
    })

    it('cancelling does not start a re-render', () => {
      asAdmin(true)
      const { adminApi } = require('@/lib/api')
      render(<OutputLinks job={publishedJob} />)
      fireEvent.click(screen.getByTestId('admin-rerender-button'))
      fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
      expect(adminApi.rerenderJob).not.toHaveBeenCalled()
    })

    it('alerts with the server error when the re-render cannot start', async () => {
      asAdmin(true)
      const { adminApi } = require('@/lib/api')
      adminApi.rerenderJob.mockRejectedValueOnce(new Error('This job is already being re-rendered'))
      const alertSpy = jest.spyOn(window, 'alert').mockImplementation(() => {})
      render(<OutputLinks job={publishedJob} />)

      fireEvent.click(screen.getByTestId('admin-rerender-button'))
      fireEvent.click(screen.getByTestId('admin-rerender-confirm'))

      await waitFor(() => expect(alertSpy).toHaveBeenCalledWith(
        "Couldn't start the re-render: This job is already being re-rendered",
      ))
      alertSpy.mockRestore()
    })

    it('toasts a plain confirmation when nothing was left in place', async () => {
      asAdmin(true)
      const { adminApi } = require('@/lib/api')
      adminApi.rerenderJob.mockResolvedValueOnce({ status: 'processing', warnings: [] })
      render(<OutputLinks job={publishedJob} />)
      fireEvent.click(screen.getByTestId('admin-rerender-button'))
      fireEvent.click(screen.getByTestId('admin-rerender-confirm'))
      await waitFor(() => expect(mockToast).toHaveBeenCalledWith({ title: 'Re-render started.' }))
    })

    it('lists outputs kept in place after starting', async () => {
      asAdmin(true)
      const { adminApi } = require('@/lib/api')
      adminApi.rerenderJob.mockResolvedValueOnce({
        status: 'processing',
        warnings: ['youtube output left in place (not re-published): YouTube upload is disabled for this job'],
      })
      render(<OutputLinks job={publishedJob} />)
      fireEvent.click(screen.getByTestId('admin-rerender-button'))
      fireEvent.click(screen.getByTestId('admin-rerender-confirm'))
      await waitFor(() => expect(mockToast).toHaveBeenCalled())
      const arg = mockToast.mock.calls[0][0]
      expect(arg.title).toBe('Re-render started. Some published outputs were left in place:')
      render(<>{arg.description}</>)
      expect(screen.getByTestId('admin-rerender-warnings')).toHaveTextContent('YouTube upload is disabled for this job')
    })
  })

  describe('Regenerate video (storage retention)', () => {
    const archivedJob: Job = {
      ...baseJob,
      renders_purged_at: '2026-09-01T00:00:00Z',
      file_urls: {
        finals: { lossy_720p_mp4: 'gs://bucket/finals/720p.mp4' },
        packages: { cdg_zip: 'gs://bucket/packages/cdg.zip' },
      },
    }

    it('shows the button and the kept downloads for an archived track', () => {
      render(<OutputLinks job={archivedJob} />)
      expect(screen.getByTestId('regenerate-button')).toHaveTextContent('Regenerate video')
      expect(screen.getByText('720p Video')).toBeInTheDocument()
      expect(screen.queryByText('4K Video')).not.toBeInTheDocument()
    })

    it('hides the button when all finals are present', () => {
      render(<OutputLinks job={baseJob} />)
      expect(screen.queryByTestId('regenerate-button')).not.toBeInTheDocument()
    })

    it('hides the button while the track is not complete', () => {
      render(<OutputLinks job={{ ...archivedJob, status: 'rendering_video' }} />)
      expect(screen.queryByTestId('regenerate-button')).not.toBeInTheDocument()
    })

    it('confirms then calls the regenerate API', async () => {
      const { api } = require('@/lib/api')
      const onJobUpdated = jest.fn()
      render(<OutputLinks job={archivedJob} onJobUpdated={onJobUpdated} />)
      fireEvent.click(screen.getByTestId('regenerate-button'))
      expect(screen.getByTestId('regenerate-dialog')).toBeInTheDocument()
      fireEvent.click(screen.getByTestId('regenerate-confirm'))
      await waitFor(() => expect(api.regenerateJob).toHaveBeenCalledWith('test-123'))
      await waitFor(() => expect(onJobUpdated).toHaveBeenCalled())
      expect(mockToast).toHaveBeenCalled()
    })
  })
})
