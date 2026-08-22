/**
 * @jest-environment jsdom
 *
 * Tests for apiFetch — the timeout + safe-retry wrapper that all backend calls go
 * through (lib/api.ts). In jsdom the hostname is "localhost", so API_BASE_URL is ""
 * and backend calls are addressed with relative "/api/..." URLs.
 */

import { apiFetch, BackendUnavailableError } from '@/lib/api'
import { getBackendStatus, reportBackendOnline } from '@/lib/backend-status'

function mockResponse(status: number, body: unknown = {}): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response
}

describe('apiFetch', () => {
  const fetchMock = jest.fn()

  beforeEach(() => {
    fetchMock.mockReset()
    ;(globalThis as unknown as { fetch: unknown }).fetch = fetchMock
    reportBackendOnline()
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it('returns the response and marks the backend online on a successful GET', async () => {
    fetchMock.mockResolvedValue(mockResponse(200, { ok: true }))

    const res = await apiFetch('/api/jobs/abc')

    expect(res.status).toBe(200)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(getBackendStatus()).toBe('online')
  })

  it('does NOT retry a deterministic 404 and stays online (backend was reached)', async () => {
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

    // Drive the two backoff sleeps (600ms, then 1500ms) between the 3 attempts.
    await jest.advanceTimersByTimeAsync(600)
    await jest.advanceTimersByTimeAsync(1500)

    await assertion
    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(getBackendStatus()).toBe('unavailable')
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
    expect(getBackendStatus()).toBe('unavailable')
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

  it('never auto-retries a non-GET (avoids duplicate submits) and does not hard-fail to "unavailable"', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))

    await expect(
      apiFetch('/api/jobs/create-from-url', { method: 'POST', body: '{}' }),
    ).rejects.toBeInstanceOf(BackendUnavailableError)

    expect(fetchMock).toHaveBeenCalledTimes(1)
    // A single mutation blip shows the subtle hint, not the full "unavailable" banner.
    expect(getBackendStatus()).toBe('reconnecting')
  })

  it('passes non-backend URLs (e.g. GCS uploads) straight through with the original error', async () => {
    const gcsError = new TypeError('gcs unreachable')
    fetchMock.mockRejectedValue(gcsError)

    await expect(
      apiFetch('https://storage.googleapis.com/bucket/obj', { method: 'PUT', body: 'x' }),
    ).rejects.toBe(gcsError)

    expect(fetchMock).toHaveBeenCalledTimes(1)
    // Third-party hosts must not drive our backend-status banner.
    expect(getBackendStatus()).toBe('online')
  })

  it('propagates a caller-initiated abort without reporting backend trouble', async () => {
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
