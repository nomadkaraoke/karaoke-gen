/**
 * @jest-environment jsdom
 *
 * Tests for api.estimateDuration and api.confirmDuration
 */

import { api } from '@/lib/api'

// ApiError is not exported from api.ts; we test error surfacing by catching throws
// and checking .status on the thrown object (which is an ApiError instance).

describe('api.estimateDuration', () => {
  const fetchMock = jest.fn()

  beforeEach(() => {
    fetchMock.mockReset()
    ;(global as any).fetch = fetchMock
  })

  it('POSTs to /api/jobs/estimate with { url } and returns parsed body', async () => {
    const responseBody = {
      duration_seconds: 210,
      credits: 1,
      blocked: false,
      source: 'youtube',
    }
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => responseBody,
    })

    const result = await api.estimateDuration('https://youtube.com/watch?v=abc')

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toMatch(/\/api\/jobs\/estimate$/)
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({ url: 'https://youtube.com/watch?v=abc' })
    expect(init.headers['Content-Type']).toBe('application/json')

    expect(result).toEqual(responseBody)
    expect(result.duration_seconds).toBe(210)
    expect(result.credits).toBe(1)
    expect(result.blocked).toBe(false)
    expect(result.source).toBe('youtube')
  })

  it('handles null duration_seconds (unknown duration)', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({
        duration_seconds: null,
        credits: 1,
        blocked: false,
        source: 'flacfetch',
      }),
    })

    const result = await api.estimateDuration('https://example.com/audio.mp3')
    expect(result.duration_seconds).toBeNull()
    expect(result.credits).toBe(1)
  })

  it('throws on non-2xx response', async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ detail: 'Invalid URL' }),
    })

    await expect(api.estimateDuration('bad-url')).rejects.toMatchObject({
      status: 400,
    })
  })

  it('includes Authorization header when token is set', async () => {
    // Use jsdom's window.localStorage (available in @jest-environment jsdom)
    window.localStorage.setItem('karaoke_access_token', 'test-token-abc')
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({ duration_seconds: 300, credits: 1, blocked: false, source: 'yt' }),
    })

    await api.estimateDuration('https://youtube.com/watch?v=xyz')

    const [, init] = fetchMock.mock.calls[0]
    expect(init.headers['Authorization']).toBe('Bearer test-token-abc')

    // Restore
    window.localStorage.removeItem('karaoke_access_token')
  })
})

describe('api.confirmDuration', () => {
  const fetchMock = jest.fn()

  beforeEach(() => {
    fetchMock.mockReset()
    ;(global as any).fetch = fetchMock
  })

  it('POSTs to /api/jobs/{jobId}/confirm-duration with { acknowledged_credits }', async () => {
    const jobResponse = {
      job_id: 'job-123',
      status: 'pending',
      progress: 0,
      created_at: '2026-06-05T00:00:00Z',
      updated_at: '2026-06-05T00:00:00Z',
    }
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => jobResponse,
    })

    const result = await api.confirmDuration('job-123', 3)

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toMatch(/\/api\/jobs\/job-123\/confirm-duration$/)
    expect(init.method).toBe('POST')
    expect(JSON.parse(init.body)).toEqual({ acknowledged_credits: 3 })
    expect(init.headers['Content-Type']).toBe('application/json')

    expect(result.job_id).toBe('job-123')
  })

  it('throws with status 402 on insufficient credits', async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 402,
      json: async () => ({ detail: 'Insufficient credits' }),
    })

    let caughtError: any
    try {
      await api.confirmDuration('job-456', 5)
    } catch (e) {
      caughtError = e
    }

    expect(caughtError).toBeDefined()
    expect(caughtError.status).toBe(402)
    expect(caughtError.message).toContain('Insufficient credits')
  })

  it('throws with status 409 on figure change / wrong state', async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({ detail: 'Duration figure has changed' }),
    })

    let caughtError: any
    try {
      await api.confirmDuration('job-789', 2)
    } catch (e) {
      caughtError = e
    }

    expect(caughtError).toBeDefined()
    expect(caughtError.status).toBe(409)
  })

  it('callers can distinguish 402 from 409 via .status', async () => {
    // 402 path
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 402,
      json: async () => ({ detail: 'Insufficient credits' }),
    })
    const err402 = await api.confirmDuration('j1', 3).catch((e) => e)
    expect(err402.status).toBe(402)

    // 409 path
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 409,
      json: async () => ({ detail: 'State mismatch' }),
    })
    const err409 = await api.confirmDuration('j2', 3).catch((e) => e)
    expect(err409.status).toBe(409)

    // The two errors are distinguishable by .status
    expect(err402.status).not.toBe(err409.status)
  })
})
