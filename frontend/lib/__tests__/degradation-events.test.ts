/**
 * @jest-environment jsdom
 * @jest-environment-options {"url": "https://gen.nomadkaraoke.com/app/jobs?x=1#/abc12345/review"}
 */

import {
  reportDegradationEvent,
  jobIdFromLocation,
  startDegradationEpisode,
  endDegradationEpisode,
  getTabId,
  FINGERPRINT_WAIT_MS,
  __resetDegradationEventsForTest,
} from '@/lib/degradation-events'
import { getDeviceFingerprint } from '@/lib/fingerprint'

jest.mock('@/lib/fingerprint', () => ({
  getDeviceFingerprint: jest.fn(),
}))

const fingerprintMock = getDeviceFingerprint as jest.MockedFunction<typeof getDeviceFingerprint>

/** Let the reporter's async fingerprint → fetch chain run (real timers). */
async function flush() {
  for (let i = 0; i < 10; i++) await Promise.resolve()
  await new Promise((r) => setTimeout(r, 0))
}

function lastCall(fetchMock: jest.Mock) {
  const [url, init] = fetchMock.mock.calls[fetchMock.mock.calls.length - 1]
  return {
    url: url as string,
    init: init as RequestInit,
    headers: (init as RequestInit).headers as Record<string, string>,
    body: JSON.parse((init as RequestInit).body as string),
  }
}

describe('degradation-events reporter', () => {
  let fetchMock: jest.Mock

  beforeEach(() => {
    __resetDegradationEventsForTest()
    window.localStorage.clear()
    window.sessionStorage.clear()
    fingerprintMock.mockReset()
    fingerprintMock.mockResolvedValue('fp-123')
    fetchMock = jest.fn(() => Promise.resolve({ ok: true }))
    global.fetch = fetchMock as unknown as typeof fetch
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it('POSTs the event with sanitized url, job id and detail', async () => {
    reportDegradationEvent('banner_unavailable', { stall_ms: 21000, probe_ok: false })
    await flush()

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const { url, init, body } = lastCall(fetchMock)
    expect(url).toBe('https://api.nomadkaraoke.com/api/client-events')
    expect(body.type).toBe('banner_unavailable')
    expect(body.url).not.toContain('x=1') // query stripped
    expect(body.job_id).toBe('abc12345')
    expect(body.detail).toEqual({ stall_ms: 21000, probe_ok: false })
    expect(init.keepalive).toBe(true)
  })

  it('never sends user_email in the body', async () => {
    window.localStorage.setItem('karaoke_access_token', 'tok-abc')
    reportDegradationEvent('banner_waking')
    await flush()
    expect(lastCall(fetchMock).body).not.toHaveProperty('user_email')
  })

  it('sends a Bearer Authorization header when an access token exists', async () => {
    window.localStorage.setItem('karaoke_access_token', 'tok-abc')
    reportDegradationEvent('banner_waking')
    await flush()
    expect(lastCall(fetchMock).headers.Authorization).toBe('Bearer tok-abc')
  })

  it('omits the Authorization header when signed out', async () => {
    reportDegradationEvent('banner_waking')
    await flush()
    const { headers } = lastCall(fetchMock)
    expect(headers).not.toHaveProperty('Authorization')
    expect(headers['Content-Type']).toBe('application/json')
  })

  it('includes the device fingerprint', async () => {
    reportDegradationEvent('lyrics_load_failed')
    await flush()
    expect(lastCall(fetchMock).body.device_fingerprint).toBe('fp-123')
  })

  it('sends a null fingerprint when fingerprinting fails', async () => {
    fingerprintMock.mockRejectedValue(new Error('blocked'))
    reportDegradationEvent('lyrics_load_failed')
    await flush()
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(lastCall(fetchMock).body.device_fingerprint).toBeNull()
  })

  it('sends without the fingerprint after the wait budget elapses', async () => {
    jest.useFakeTimers()
    fingerprintMock.mockImplementation(() => new Promise(() => {})) // never resolves
    reportDegradationEvent('waveform_slow')

    // Let the dynamic import settle; still waiting on the fingerprint.
    for (let i = 0; i < 10; i++) await Promise.resolve()
    expect(fetchMock).not.toHaveBeenCalled()

    await jest.advanceTimersByTimeAsync(FINGERPRINT_WAIT_MS)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(lastCall(fetchMock).body.device_fingerprint).toBeNull()
  })

  it('attaches a tab_id that is stable within the tab', async () => {
    reportDegradationEvent('banner_waking')
    reportDegradationEvent('waveform_failed')
    await flush()
    const a = JSON.parse((fetchMock.mock.calls[0][1] as RequestInit).body as string)
    const b = JSON.parse((fetchMock.mock.calls[1][1] as RequestInit).body as string)
    expect(typeof a.tab_id).toBe('string')
    expect(a.tab_id.length).toBeGreaterThan(0)
    expect(b.tab_id).toBe(a.tab_id)
    expect(window.sessionStorage.getItem('nk_tab_id')).toBe(a.tab_id)
    expect(getTabId()).toBe(a.tab_id)
  })

  it('attaches episode_id only while an episode is active', async () => {
    reportDegradationEvent('waveform_slow')
    const id = startDegradationEpisode()
    expect(startDegradationEpisode()).toBe(id) // idempotent while open
    reportDegradationEvent('banner_reconnecting')
    endDegradationEpisode()
    reportDegradationEvent('waveform_failed')
    await flush()

    const bodies = fetchMock.mock.calls.map((c) => JSON.parse((c[1] as RequestInit).body as string))
    expect(bodies.map((b) => b.type)).toEqual(['waveform_slow', 'banner_reconnecting', 'waveform_failed'])
    expect(bodies[0].episode_id).toBeNull()
    expect(bodies[1].episode_id).toBe(id)
    expect(bodies[2].episode_id).toBeNull()
  })

  it('keeps the episode_id captured at report time even if the episode ends before send', async () => {
    const id = startDegradationEpisode()
    reportDegradationEvent('banner_recovered', { duration_ms: 5000 })
    endDegradationEpisode()
    await flush()
    expect(lastCall(fetchMock).body.episode_id).toBe(id)
  })

  it('uses snake_case top-level wire fields', async () => {
    reportDegradationEvent('banner_waking')
    await flush()
    expect(Object.keys(lastCall(fetchMock).body).sort()).toEqual(
      [
        'detail',
        'device_fingerprint',
        'episode_id',
        'job_id',
        'locale',
        'release',
        'tab_id',
        'type',
        'url',
      ].sort(),
    )
  })

  it('throttles repeat reports of the same type', async () => {
    reportDegradationEvent('banner_reconnecting')
    reportDegradationEvent('banner_reconnecting')
    await flush()
    expect(fetchMock).toHaveBeenCalledTimes(1)

    // A different type is NOT throttled by the first one.
    reportDegradationEvent('waveform_failed')
    await flush()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('does not throttle banner_recovered', async () => {
    reportDegradationEvent('banner_recovered', { duration_ms: 1 })
    reportDegradationEvent('banner_recovered', { duration_ms: 2 })
    await flush()
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('never throws when fetch rejects', async () => {
    fetchMock.mockImplementation(() => Promise.reject(new Error('offline')))
    expect(() => reportDegradationEvent('waveform_slow')).not.toThrow()
    await flush()
  })

  it('never throws when fetch throws synchronously', async () => {
    fetchMock.mockImplementation(() => {
      throw new Error('boom')
    })
    expect(() => reportDegradationEvent('waveform_slow')).not.toThrow()
    await flush()
  })
})

describe('jobIdFromLocation', () => {
  it('parses hash-router review URLs', () => {
    expect(jobIdFromLocation('https://gen.nomadkaraoke.com/app/jobs#/d93747cd/review')).toBe('d93747cd')
  })

  it('parses path-style job URLs', () => {
    expect(jobIdFromLocation('https://gen.nomadkaraoke.com/en/jobs/0370244a')).toBe('0370244a')
  })

  it('returns null when no job id is present', () => {
    expect(jobIdFromLocation('https://gen.nomadkaraoke.com/')).toBeNull()
    expect(jobIdFromLocation('not a url')).toBeNull()
  })
})
