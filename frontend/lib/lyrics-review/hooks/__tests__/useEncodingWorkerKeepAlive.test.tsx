import { renderHook, act } from '@testing-library/react'
import {
  useEncodingWorkerKeepAlive,
  REVIEW_HEARTBEAT_INTERVAL_MS,
  REVIEW_ACTIVE_WINDOW_MS,
  HEARTBEAT_MIN_GAP_MS,
} from '../useEncodingWorkerKeepAlive'
import { warmupEncodingWorker, heartbeatEncodingWorker } from '@/lib/api'

jest.mock('@/lib/api', () => ({
  warmupEncodingWorker: jest.fn(),
  heartbeatEncodingWorker: jest.fn(),
}))

const warmup = warmupEncodingWorker as jest.Mock
const heartbeat = heartbeatEncodingWorker as jest.Mock

function setVisibility(state: 'visible' | 'hidden') {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state })
  document.dispatchEvent(new Event('visibilitychange'))
}

describe('useEncodingWorkerKeepAlive', () => {
  beforeEach(() => {
    jest.useFakeTimers()
    warmup.mockClear()
    heartbeat.mockClear()
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' })
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it('heartbeat interval stays well under the 5-minute backend idle timeout', () => {
    expect(REVIEW_HEARTBEAT_INTERVAL_MS * 2).toBeLessThanOrEqual(5 * 60_000)
  })

  it('warms up once on mount', () => {
    renderHook(() => useEncodingWorkerKeepAlive('job-1', true))
    expect(warmup).toHaveBeenCalledTimes(1)
    expect(warmup).toHaveBeenCalledWith('job-1')
  })

  it('does nothing when disabled (read-only) or without a job id', () => {
    renderHook(() => useEncodingWorkerKeepAlive('job-1', false))
    renderHook(() => useEncodingWorkerKeepAlive(undefined, true))
    act(() => {
      jest.advanceTimersByTime(REVIEW_HEARTBEAT_INTERVAL_MS * 3)
    })
    expect(warmup).not.toHaveBeenCalled()
    expect(heartbeat).not.toHaveBeenCalled()
  })

  it('heartbeats periodically while visible even without edits', () => {
    renderHook(() => useEncodingWorkerKeepAlive('job-1', true))
    act(() => {
      jest.advanceTimersByTime(REVIEW_HEARTBEAT_INTERVAL_MS * 3)
    })
    expect(heartbeat).toHaveBeenCalledTimes(3)
  })

  it('skips periodic heartbeats while the tab is hidden', () => {
    renderHook(() => useEncodingWorkerKeepAlive('job-1', true))
    act(() => {
      setVisibility('hidden')
      jest.advanceTimersByTime(REVIEW_HEARTBEAT_INTERVAL_MS * 3)
    })
    expect(heartbeat).not.toHaveBeenCalled()
  })

  it('re-warms the worker when the tab becomes visible again', () => {
    renderHook(() => useEncodingWorkerKeepAlive('job-1', true))
    expect(warmup).toHaveBeenCalledTimes(1)
    act(() => {
      setVisibility('hidden')
      setVisibility('visible')
    })
    expect(warmup).toHaveBeenCalledTimes(2)
  })

  it('stops keeping the VM alive after the reviewer goes inactive', () => {
    renderHook(() => useEncodingWorkerKeepAlive('job-1', true))
    act(() => {
      jest.advanceTimersByTime(REVIEW_ACTIVE_WINDOW_MS + REVIEW_HEARTBEAT_INTERVAL_MS * 3)
    })
    const callsWhileIdle = heartbeat.mock.calls.length
    act(() => {
      jest.advanceTimersByTime(REVIEW_HEARTBEAT_INTERVAL_MS * 3)
    })
    expect(heartbeat.mock.calls.length).toBe(callsWhileIdle)

    // Any interaction resumes the keep-alive.
    act(() => {
      window.dispatchEvent(new Event('pointerdown'))
      jest.advanceTimersByTime(REVIEW_HEARTBEAT_INTERVAL_MS)
    })
    expect(heartbeat.mock.calls.length).toBe(callsWhileIdle + 1)
  })

  it('debounces edit-triggered heartbeats', () => {
    const { result } = renderHook(() => useEncodingWorkerKeepAlive('job-1', true))
    act(() => {
      result.current.sendHeartbeat()
      result.current.sendHeartbeat()
    })
    expect(heartbeat).toHaveBeenCalledTimes(1)
    act(() => {
      jest.advanceTimersByTime(HEARTBEAT_MIN_GAP_MS + 1)
      result.current.sendHeartbeat()
    })
    expect(heartbeat).toHaveBeenCalledTimes(2)
  })
})
