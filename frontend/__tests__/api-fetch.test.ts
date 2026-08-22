/**
 * @jest-environment jsdom
 *
 * Tests for apiFetch — the timeout + safe-retry wrapper that all backend calls go
 * through (lib/api.ts). In jsdom the hostname is "localhost", so API_BASE_URL is ""
 * and backend calls are addressed with relative "/api/..." URLs.
 */

import { apiFetch, BackendUnavailableError } from '@/lib/api'
import {
  getBackendStatus,
  STALL_RECONNECTING_MS,
  STALL_UNAVAILABLE_MS,
} from '@/lib/backend-status'

function mockResponse(status: number, body: unknown = {}): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response
}

/** A fetch mock that returns a promise you resolve/reject by hand, so a request can
 *  be held "in flight" under fake timers and then settled to clean up. */
function deferredFetch() {
  let settle!: (r: Response) => void
  let fail!: (e: unknown) => void
  const fn = jest.fn(
    () =>
      new Promise<Response>((res, rej) => {
        settle = res
        fail = rej
      }),
  )
  return { fn, settle: (r: Response) => settle(r), fail: (e: unknown) => fail(e) }
}

describe('apiFetch', () => {
  const fetchMock = jest.fn()

  beforeEach(() => {
    fetchMock.mockReset()
    ;(globalThis as unknown as { fetch: unknown }).fetch = fetchMock
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it('returns the response and stays online on a fast successful GET', async () => {
    fetchMock.mockResolvedValue(mockResponse(200, { ok: true }))
    const res = await apiFetch('/api/jobs/abc')
    expect(res.status).toBe(200)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(getBackendStatus()).toBe('online')
  })

  it('does NOT retry a deterministic 404 and stays online', async () => {
    fetchMock.mockResolvedValue(mockResponse(404))
    const res = await apiFetch('/api/jobs/missing')
    expect(res.status).toBe(404)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(getBackendStatus()).toBe('online')
  })

  it('retries a GET on repeated network failure, then throws BackendUnavailableError', async () => {
    jest.useFakeTimers()
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))

    const p = apiFetch('/api/jobs/abc')
    const assertion = expect(p).rejects.toBeInstanceOf(BackendUnavailableError)
    await jest.advanceTimersByTimeAsync(600)
    await jest.advanceTimersByTimeAsync(1500)
    await assertion

    expect(fetchMock).toHaveBeenCalledTimes(3)
    // A fast total failure (~2s) is NOT a stall, so the banner never appears.
    expect(getBackendStatus()).toBe('online')
  })

  it('retries a GET that keeps returning a transient 503, then throws', async () => {
    jest.useFakeTimers()
    fetchMock.mockResolvedValue(mockResponse(503))

    const p = apiFetch('/api/jobs/abc')
    const assertion = expect(p).rejects.toBeInstanceOf(BackendUnavailableError)
    await jest.advanceTimersByTimeAsync(600)
    await jest.advanceTimersByTimeAsync(1500)
    await assertion

    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(getBackendStatus()).toBe('online')
  })

  it('recovers transparently when a retried GET succeeds on a later attempt', async () => {
    jest.useFakeTimers()
    fetchMock
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce(mockResponse(200, { ok: true }))

    const p = apiFetch('/api/jobs/abc')
    await jest.advanceTimersByTimeAsync(600)
    const res = await p

    expect(res.status).toBe(200)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(getBackendStatus()).toBe('online')
  })

  it('surfaces the banner only once a GET has STALLED past the thresholds, then clears', async () => {
    jest.useFakeTimers()
    const d = deferredFetch()
    fetchMock.mockImplementation(d.fn)

    const p = apiFetch('/api/jobs/abc')
    p.catch(() => {}) // avoid unhandled rejection if it ever fails

    expect(getBackendStatus()).toBe('online')
    await jest.advanceTimersByTimeAsync(STALL_RECONNECTING_MS)
    expect(getBackendStatus()).toBe('reconnecting')
    await jest.advanceTimersByTimeAsync(STALL_UNAVAILABLE_MS - STALL_RECONNECTING_MS)
    expect(getBackendStatus()).toBe('unavailable')

    d.settle(mockResponse(200, { ok: true }))
    await p
    expect(getBackendStatus()).toBe('online')
  })

  it('never surfaces the banner for a slow POST (mutations are not tracked)', async () => {
    jest.useFakeTimers()
    const d = deferredFetch()
    fetchMock.mockImplementation(d.fn)

    const p = apiFetch('/api/jobs/create-from-url', { method: 'POST', body: '{}' })
    p.catch(() => {})

    await jest.advanceTimersByTimeAsync(STALL_UNAVAILABLE_MS + 5000)
    expect(getBackendStatus()).toBe('online')

    d.settle(mockResponse(200, { ok: true }))
    await p
  })

  it('does NOT impose a hard timeout on a long-running POST (e.g. preview render)', async () => {
    jest.useFakeTimers()
    let capturedSignal: AbortSignal | undefined
    const d = deferredFetch()
    fetchMock.mockImplementation((_url: unknown, init: RequestInit) => {
      capturedSignal = init.signal ?? undefined
      return d.fn()
    })

    const p = apiFetch('/api/review/abc/preview-video', { method: 'POST', body: '{}' })
    p.catch(() => {})

    // Well past the GET hard-timeout (45s): a POST must still be running, unaborted.
    await jest.advanceTimersByTimeAsync(60_000)
    expect(capturedSignal?.aborted).toBe(false)

    d.settle(mockResponse(200, { ok: true }))
    await expect(p).resolves.toBeDefined()
  })

  it('never auto-retries a non-GET, even on network failure', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
    await expect(
      apiFetch('/api/jobs/create-from-url', { method: 'POST', body: '{}' }),
    ).rejects.toBeInstanceOf(BackendUnavailableError)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(getBackendStatus()).toBe('online')
  })

  it('reads method + signal from a Request input (a POST Request is not retried)', async () => {
    // jsdom has no global Request; provide a minimal stand-in with the fields
    // apiFetch reads (url/method/signal) so `input instanceof Request` engages.
    class FakeRequest {
      url: string
      method: string
      signal?: AbortSignal
      constructor(url: string, init?: { method?: string; signal?: AbortSignal }) {
        this.url = url
        this.method = init?.method ?? 'GET'
        this.signal = init?.signal
      }
    }
    ;(globalThis as unknown as { Request: unknown }).Request = FakeRequest

    try {
      fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
      const req = new FakeRequest('http://localhost/api/jobs/create-from-url', {
        method: 'POST',
      })
      await expect(apiFetch(req as unknown as Request)).rejects.toBeInstanceOf(
        BackendUnavailableError,
      )
      expect(fetchMock).toHaveBeenCalledTimes(1)
    } finally {
      delete (globalThis as unknown as { Request?: unknown }).Request
    }
  })

  it('passes non-backend URLs (e.g. GCS uploads) straight through with the original error', async () => {
    const gcsError = new TypeError('gcs unreachable')
    fetchMock.mockRejectedValue(gcsError)
    await expect(
      apiFetch('https://storage.googleapis.com/bucket/obj', { method: 'PUT', body: 'x' }),
    ).rejects.toBe(gcsError)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(getBackendStatus()).toBe('online')
  })

  it('propagates a caller-initiated abort without surfacing the banner', async () => {
    const abortErr = new DOMException('Aborted', 'AbortError')
    fetchMock.mockRejectedValue(abortErr)
    const controller = new AbortController()
    controller.abort()
    await expect(
      apiFetch('/api/jobs/abc', { signal: controller.signal }),
    ).rejects.toBe(abortErr)
    expect(getBackendStatus()).toBe('online')
  })
})
