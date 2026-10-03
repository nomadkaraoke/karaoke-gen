/**
 * @jest-environment jsdom
 */

import { act, fireEvent, render, screen } from '@testing-library/react'
import type { BackendStatus } from '@/lib/backend-status'

let mockStatus: BackendStatus = 'online'

jest.mock('@/lib/backend-status', () => ({
  useBackendStatus: () => mockStatus,
  getBackendStatusDebug: () => ({
    oldestStallMs: 0,
    inFlightCount: 0,
    lastProbeOk: null,
    consecutiveProbeFailures: 0,
    sinceReachableMs: 0,
  }),
  installBackendPrewarm: jest.fn(),
  __installBackendStatusDevHook: jest.fn(),
}))

jest.mock('@/lib/api', () => ({ __backendPrewarm: jest.fn() }))

jest.mock('@/lib/degradation-events', () => ({
  reportDegradationEvent: jest.fn(),
  startDegradationEpisode: jest.fn(() => 'ep-1'),
  endDegradationEpisode: jest.fn(),
}))

import { BackendStatusBanner } from '@/components/backend-status-banner'
import {
  reportDegradationEvent,
  startDegradationEpisode,
  endDegradationEpisode,
} from '@/lib/degradation-events'

const reportMock = reportDegradationEvent as jest.Mock
const startMock = startDegradationEpisode as jest.Mock
const endMock = endDegradationEpisode as jest.Mock

function recoveredCalls() {
  return reportMock.mock.calls.filter((c) => c[0] === 'banner_recovered')
}

describe('BackendStatusBanner episode telemetry', () => {
  let now: number
  let nowSpy: jest.SpyInstance

  beforeEach(() => {
    mockStatus = 'online'
    reportMock.mockClear()
    startMock.mockClear()
    endMock.mockClear()
    now = 1_000_000
    nowSpy = jest.spyOn(Date, 'now').mockImplementation(() => now)
  })

  afterEach(() => {
    nowSpy.mockRestore()
  })

  it('reports nothing when status never leaves online', () => {
    const { rerender } = render(<BackendStatusBanner />)
    rerender(<BackendStatusBanner />)
    expect(reportMock).not.toHaveBeenCalled()
    expect(startMock).not.toHaveBeenCalled()
    expect(endMock).not.toHaveBeenCalled()
  })

  it('reports exactly one banner_recovered with duration and peak after offline → online', () => {
    const { rerender } = render(<BackendStatusBanner />)

    mockStatus = 'reconnecting'
    rerender(<BackendStatusBanner />)
    expect(startMock).toHaveBeenCalledTimes(1)
    // Episode starts before the banner event is reported, so it carries the id.
    expect(startMock.mock.invocationCallOrder[0]).toBeLessThan(
      reportMock.mock.invocationCallOrder[0],
    )
    expect(reportMock).toHaveBeenCalledWith('banner_reconnecting', expect.any(Object))

    now += 5_000
    mockStatus = 'unavailable'
    rerender(<BackendStatusBanner />)
    now += 2_000
    mockStatus = 'reconnecting'
    rerender(<BackendStatusBanner />)
    expect(startMock).toHaveBeenCalledTimes(1) // same episode

    now += 3_000
    mockStatus = 'online'
    rerender(<BackendStatusBanner />)
    rerender(<BackendStatusBanner />) // extra re-renders must not double-report

    expect(recoveredCalls()).toHaveLength(1)
    expect(recoveredCalls()[0][1]).toEqual({
      duration_ms: 10_000,
      peak_status: 'unavailable',
      dismissed: false,
    })
    expect(endMock).toHaveBeenCalledTimes(1)
  })

  it('starts a fresh episode for a later outage', () => {
    const { rerender } = render(<BackendStatusBanner />)
    mockStatus = 'waking'
    rerender(<BackendStatusBanner />)
    now += 1_000
    mockStatus = 'online'
    rerender(<BackendStatusBanner />)

    mockStatus = 'reconnecting'
    rerender(<BackendStatusBanner />)
    now += 4_000
    mockStatus = 'online'
    rerender(<BackendStatusBanner />)

    expect(startMock).toHaveBeenCalledTimes(2)
    expect(recoveredCalls().map((c) => c[1])).toEqual([
      { duration_ms: 1_000, peak_status: 'waking', dismissed: false },
      { duration_ms: 4_000, peak_status: 'reconnecting', dismissed: false },
    ])
  })

  it('records a dismissed unavailable card and does not report it again', () => {
    const { rerender } = render(<BackendStatusBanner />)
    mockStatus = 'unavailable'
    rerender(<BackendStatusBanner />)
    expect(reportMock).toHaveBeenCalledWith('banner_unavailable', expect.any(Object))

    act(() => {
      fireEvent.click(screen.getByRole('button'))
    })
    const unavailableCount = reportMock.mock.calls.filter((c) => c[0] === 'banner_unavailable').length
    expect(unavailableCount).toBe(1)

    now += 8_000
    mockStatus = 'online'
    rerender(<BackendStatusBanner />)
    expect(recoveredCalls()).toHaveLength(1)
    expect(recoveredCalls()[0][1]).toEqual({
      duration_ms: 8_000,
      peak_status: 'unavailable',
      dismissed: true,
    })
  })
})
