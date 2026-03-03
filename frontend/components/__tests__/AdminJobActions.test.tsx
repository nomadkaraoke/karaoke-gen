/**
 * Tests for AdminJobActions component
 */

import { render, screen } from '@testing-library/react'
import { AdminJobActions } from '../job/AdminJobActions'
import { Job } from '@/lib/api'

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
  })

  it('renders all admin action buttons', () => {
    render(<AdminJobActions job={mockJob} onRefresh={mockOnRefresh} />)

    // Reset buttons
    expect(screen.getByText('Audio')).toBeInTheDocument()
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

  it('stops event propagation on click', () => {
    const { container } = render(<AdminJobActions job={mockJob} onRefresh={mockOnRefresh} />)

    const wrapper = container.firstChild as HTMLElement
    const event = new MouseEvent('click', { bubbles: true })
    const stopPropagation = jest.spyOn(event, 'stopPropagation')

    wrapper.dispatchEvent(event)
    expect(stopPropagation).toHaveBeenCalled()
  })
})
