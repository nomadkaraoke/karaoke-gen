/**
 * Tests for AdminJobActions component
 */

import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { AdminJobActions } from '../job/AdminJobActions'
import { Job, adminApi } from '@/lib/api'

// Mock the API module
jest.mock('@/lib/api', () => ({
  adminApi: {
    resetJob: jest.fn(),
    deleteJobOutputs: jest.fn(),
    regenerateScreens: jest.fn(),
    restartJob: jest.fn(),
    overrideAudioSource: jest.fn(),
    deleteJob: jest.fn(),
  }
}))

// Mock the toast hook so we can assert on what gets shown to the user
const mockToast = jest.fn()
jest.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: mockToast }),
}))

const mockAdminApi = adminApi as jest.Mocked<typeof adminApi>

describe('AdminJobActions', () => {
  const mockOnRefresh = jest.fn()

  const mockJob: Job = {
    job_id: '123',
    status: 'complete',
    progress: 100,
    created_at: '2025-01-01T00:00:00Z',
    updated_at: '2025-01-01T00:00:00Z',
    artist: 'Test Artist',
    title: 'Test Song',
  }

  beforeEach(() => {
    jest.clearAllMocks()
    // Auto-confirm the native confirm() dialogs the component uses
    jest.spyOn(window, 'confirm').mockReturnValue(true)
  })

  afterEach(() => {
    jest.restoreAllMocks()
  })

  it('renders all admin action buttons', () => {
    render(<AdminJobActions job={mockJob} onRefresh={mockOnRefresh} />)

    // Reset buttons
    expect(screen.getByText('Audio')).toBeInTheDocument()
    expect(screen.getByText('Audio Edit')).toBeInTheDocument()
    expect(screen.getByText('Review')).toBeInTheDocument()
    expect(screen.getByText('Reprocess')).toBeInTheDocument()

    // Other action buttons
    expect(screen.getByText('Del Outputs')).toBeInTheDocument()
    expect(screen.getByText('Regen Screens')).toBeInTheDocument()
    expect(screen.getByText('Full Restart')).toBeInTheDocument()
    expect(screen.getByText('Audio Search')).toBeInTheDocument()
    expect(screen.getByText('Delete')).toBeInTheDocument()
  })

  it('renders Reset label', () => {
    render(<AdminJobActions job={mockJob} onRefresh={mockOnRefresh} />)

    expect(screen.getByText('Reset:')).toBeInTheDocument()
  })

  it('shows a useful error toast when regenerate screens fails', async () => {
    mockAdminApi.regenerateScreens.mockRejectedValue(
      new Error("Cannot regenerate screens for job in 'in_review' state.")
    )

    render(<AdminJobActions job={mockJob} onRefresh={mockOnRefresh} />)
    fireEvent.click(screen.getByText('Regen Screens'))

    await waitFor(() => {
      expect(mockToast).toHaveBeenCalledWith(
        expect.objectContaining({
          title: 'Regenerate Screens Failed',
          description: "Cannot regenerate screens for job in 'in_review' state.",
          variant: 'destructive',
        })
      )
    })
    // A failed action must not silently report success / refresh
    expect(mockOnRefresh).not.toHaveBeenCalled()
  })

  it('falls back to a human-readable error when the failure has no message', async () => {
    mockAdminApi.regenerateScreens.mockRejectedValue(new Error(''))

    render(<AdminJobActions job={mockJob} onRefresh={mockOnRefresh} />)
    fireEvent.click(screen.getByText('Regen Screens'))

    await waitFor(() => {
      expect(mockToast).toHaveBeenCalledWith(
        expect.objectContaining({
          title: 'Regenerate Screens Failed',
          variant: 'destructive',
        })
      )
    })
    const { description } = mockToast.mock.calls[0][0]
    expect(description).toContain('check the job logs')
  })

  it('stops event propagation on click', () => {
    const { container } = render(<AdminJobActions job={mockJob} onRefresh={mockOnRefresh} />)

    const wrapper = container.firstChild as HTMLElement
    const event = new MouseEvent('click', { bubbles: true })
    const stopPropagation = jest.spyOn(event, 'stopPropagation')

    wrapper.dispatchEvent(event)
    expect(stopPropagation).toHaveBeenCalled()
  })
})
