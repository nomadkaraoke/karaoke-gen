/**
 * @jest-environment jsdom
 * @jest-environment-options {"url": "https://gen.nomadkaraoke.com/app/jobs?x=1#/abc12345/review"}
 */

import {
  reportDegradationEvent,
  jobIdFromLocation,
  __resetDegradationEventsForTest,
} from '@/lib/degradation-events'

describe('degradation-events reporter', () => {
  let fetchMock: jest.Mock

  beforeEach(() => {
    __resetDegradationEventsForTest()
    fetchMock = jest.fn(() => Promise.resolve({ ok: true }))
    global.fetch = fetchMock as unknown as typeof fetch
  })

  it('POSTs the event with sanitized url, job id and detail', () => {
    reportDegradationEvent('banner_unavailable', { stall_ms: 21000, probe_ok: false })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('https://api.nomadkaraoke.com/api/client-events')
    const body = JSON.parse((init as RequestInit).body as string)
    expect(body.type).toBe('banner_unavailable')
    expect(body.url).not.toContain('x=1') // query stripped
    expect(body.job_id).toBe('abc12345')
    expect(body.detail).toEqual({ stall_ms: 21000, probe_ok: false })
    expect((init as RequestInit).keepalive).toBe(true)
  })

  it('throttles repeat reports of the same type', () => {
    reportDegradationEvent('banner_reconnecting')
    reportDegradationEvent('banner_reconnecting')
    expect(fetchMock).toHaveBeenCalledTimes(1)

    // A different type is NOT throttled by the first one.
    reportDegradationEvent('waveform_failed')
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it('never throws when fetch rejects', () => {
    fetchMock.mockImplementation(() => Promise.reject(new Error('offline')))
    expect(() => reportDegradationEvent('waveform_slow')).not.toThrow()
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
