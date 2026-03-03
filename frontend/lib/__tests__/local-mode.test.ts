/**
 * Unit tests for local-mode.ts
 *
 * These tests verify the isLocalMode() function correctly distinguishes between:
 * - Local CLI mode (path-based routing on localhost)
 * - Cloud mode (hash-based routing, even on localhost)
 * - Production (not on localhost)
 */

import {
  isLocalMode,
  getLocalJobId,
  isLocalReview,
  LOCAL_MODE_USER,
  createLocalModeJob,
} from '../local-mode'

// Mock window.location
const mockWindowLocation = (overrides: Partial<Location>) => {
  const originalLocation = window.location
  delete (window as unknown as { location?: Location }).location
  window.location = {
    ...originalLocation,
    hostname: 'localhost',
    port: '3000',
    hash: '',
    pathname: '/',
    ...overrides,
  } as Location
  return () => {
    window.location = originalLocation
  }
}

describe('isLocalMode', () => {
  describe('returns false when not on localhost', () => {
    it('returns false for production hostname', () => {
      const restore = mockWindowLocation({
        hostname: 'gen.nomadkaraoke.com',
        port: '',
        hash: '',
        pathname: '/app/jobs/abc123/review',
      })

      expect(isLocalMode()).toBe(false)
      restore()
    })

    it('returns false for custom domain', () => {
      const restore = mockWindowLocation({
        hostname: 'example.com',
        port: '443',
        hash: '',
        pathname: '/app',
      })

      expect(isLocalMode()).toBe(false)
      restore()
    })
  })

  describe('pathname /local/ takes priority over port', () => {
    it('returns true for /local/ pathname even on non-standard port', () => {
      // Pathname check happens before port check, so /local/ in path means
      // local mode regardless of port (needed for E2E tests on arbitrary ports)
      const restore = mockWindowLocation({
        hostname: 'localhost',
        port: '5000',
        hash: '',
        pathname: '/app/jobs/local/review',
      })

      expect(isLocalMode()).toBe(true)
      restore()
    })

    it('returns false for non-local pathname on non-standard port', () => {
      const restore = mockWindowLocation({
        hostname: 'localhost',
        port: '5000',
        hash: '',
        pathname: '/app',
      })

      expect(isLocalMode()).toBe(false)
      restore()
    })
  })

  describe('returns false for cloud mode hash-based routing on localhost', () => {
    it('returns false for hash-based review route', () => {
      const restore = mockWindowLocation({
        hostname: 'localhost',
        port: '3000',
        hash: '#/abc123/review',
        pathname: '/app/jobs/',
      })

      expect(isLocalMode()).toBe(false)
      restore()
    })

    it('returns false for hash-based instrumental route', () => {
      const restore = mockWindowLocation({
        hostname: 'localhost',
        port: '3000',
        hash: '#/test-job-id/instrumental',
        pathname: '/app/jobs/',
      })

      expect(isLocalMode()).toBe(false)
      restore()
    })

    it('returns false for hash with leading slash in job id', () => {
      const restore = mockWindowLocation({
        hostname: 'localhost',
        port: '3000',
        hash: '#/some-uuid-123/review',
        pathname: '/app/jobs/',
      })

      expect(isLocalMode()).toBe(false)
      restore()
    })
  })

  describe('returns true for local CLI mode', () => {
    it('returns true for /local/ pathname on port 3000', () => {
      const restore = mockWindowLocation({
        hostname: 'localhost',
        port: '3000',
        hash: '',
        pathname: '/app/jobs/local/review',
      })

      expect(isLocalMode()).toBe(true)
      restore()
    })

    it('returns true for /local/ pathname on port 8000', () => {
      const restore = mockWindowLocation({
        hostname: 'localhost',
        port: '8000',
        hash: '',
        pathname: '/app/jobs/local/instrumental',
      })

      expect(isLocalMode()).toBe(true)
      restore()
    })

    it('returns true for 127.0.0.1 with /local/ pathname', () => {
      const restore = mockWindowLocation({
        hostname: '127.0.0.1',
        port: '3000',
        hash: '',
        pathname: '/app/jobs/local/review',
      })

      expect(isLocalMode()).toBe(true)
      restore()
    })

    it('returns true for local review server ports', () => {
      const localPorts = ['8000', '8001', '8764', '8765']

      for (const port of localPorts) {
        const restore = mockWindowLocation({
          hostname: 'localhost',
          port,
          hash: '',
          pathname: '/app/jobs/local/review',
        })

        expect(isLocalMode()).toBe(true)
        restore()
      }
    })
  })

  describe('backwards compatibility', () => {
    it('returns true for localhost with known port and no hash', () => {
      // When on localhost with known port and no explicit /local/ or hash routing,
      // default to local mode for backwards compatibility
      const restore = mockWindowLocation({
        hostname: 'localhost',
        port: '3000',
        hash: '',
        pathname: '/app',
      })

      expect(isLocalMode()).toBe(true)
      restore()
    })
  })
})

describe('getLocalJobId', () => {
  it('always returns "local"', () => {
    expect(getLocalJobId()).toBe('local')
  })
})

describe('isLocalReview', () => {
  it('returns the same value as isLocalMode', () => {
    const restore = mockWindowLocation({
      hostname: 'localhost',
      port: '3000',
      hash: '',
      pathname: '/app/jobs/local/review',
    })

    expect(isLocalReview()).toBe(isLocalMode())
    restore()
  })
})

describe('LOCAL_MODE_USER', () => {
  it('has expected properties for local mode', () => {
    expect(LOCAL_MODE_USER).toEqual({
      email: 'local@localhost',
      role: 'admin',
      credits: 999,
      display_name: 'Local User',
      total_jobs_created: 0,
      total_jobs_completed: 0,
    })
  })
})

describe('createLocalModeJob', () => {
  it('creates a job with default values', () => {
    const job = createLocalModeJob({ routeType: 'review' })

    expect(job.job_id).toBe('local')
    expect(job.status).toBe('awaiting_review')
    expect(job.artist).toBe('Local Artist')
    expect(job.title).toBe('Local Title')
    expect(job.user_email).toBe('local@localhost')
    expect(job.audio_hash).toBe('local')
  })

  it('uses provided artist and title', () => {
    const job = createLocalModeJob({
      routeType: 'instrumental',
      artist: 'Test Artist',
      title: 'Test Song',
    })

    expect(job.artist).toBe('Test Artist')
    expect(job.title).toBe('Test Song')
  })

  it('includes timestamps', () => {
    const before = new Date().toISOString()
    const job = createLocalModeJob({ routeType: 'review' })
    const after = new Date().toISOString()

    expect(job.created_at).toBeDefined()
    expect(job.updated_at).toBeDefined()
    expect(job.created_at >= before).toBe(true)
    expect(job.created_at <= after).toBe(true)
  })
})
