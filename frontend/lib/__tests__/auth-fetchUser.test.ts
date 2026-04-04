/**
 * Tests for auth store's fetchUser() behavior, specifically:
 * - Abort errors (from page navigation) should NOT clear auth
 * - Other errors (expired session) SHOULD clear auth
 */

// Mock api module before importing auth
const mockGetCurrentUser = jest.fn()
jest.mock('@/lib/api', () => ({
  api: { getCurrentUser: (...args: unknown[]) => mockGetCurrentUser(...args) },
  adminApi: {},
  setAccessToken: jest.fn(),
  clearAccessToken: jest.fn(),
  getAccessToken: jest.fn(() => 'test-token'),
}))

import { useAuth } from '../auth'
import { clearAccessToken } from '../api'

describe('fetchUser', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    // Reset store to a known state with a user
    useAuth.setState({
      user: {
        token: 'test-token',
        email: 'user@test.com',
        role: 'user',
        credits: 5,
        display_name: 'Test User',
        total_jobs_created: 1,
        total_jobs_completed: 0,
        feedback_eligible: false,
      },
      isLoading: false,
      hasHydrated: true,
    })
  })

  it('preserves auth state when fetch is aborted (page navigation)', async () => {
    // Simulate an AbortError (thrown when browser navigates away during fetch)
    mockGetCurrentUser.mockRejectedValueOnce(new DOMException('The operation was aborted.', 'AbortError'))

    const result = await useAuth.getState().fetchUser()

    expect(result).toBe(false)
    // Auth should NOT be cleared
    expect(clearAccessToken).not.toHaveBeenCalled()
    expect(useAuth.getState().user).not.toBeNull()
    expect(useAuth.getState().isLoading).toBe(false)
  })

  it('clears auth state when API returns a non-abort error (expired session)', async () => {
    mockGetCurrentUser.mockRejectedValueOnce(new Error('Unauthorized'))

    const result = await useAuth.getState().fetchUser()

    expect(result).toBe(false)
    // Auth SHOULD be cleared
    expect(clearAccessToken).toHaveBeenCalled()
    expect(useAuth.getState().user).toBeNull()
    expect(useAuth.getState().isLoading).toBe(false)
  })

  it('sets user on successful fetch', async () => {
    mockGetCurrentUser.mockResolvedValueOnce({
      user: {
        email: 'fresh@test.com',
        role: 'admin',
        credits: 10,
        display_name: 'Fresh User',
        total_jobs_created: 5,
        total_jobs_completed: 3,
        feedback_eligible: true,
      },
    })

    const result = await useAuth.getState().fetchUser()

    expect(result).toBe(true)
    expect(useAuth.getState().user?.email).toBe('fresh@test.com')
    expect(useAuth.getState().user?.role).toBe('admin')
    expect(useAuth.getState().isLoading).toBe(false)
  })
})
