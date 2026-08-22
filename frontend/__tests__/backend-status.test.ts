/**
 * @jest-environment jsdom
 *
 * Tests for the backend-connectivity status store (lib/backend-status.ts).
 * Status is derived from how long the OLDEST in-flight tracked read has been
 * outstanding — a read that completes promptly never surfaces the banner.
 */

import {
  getBackendStatus,
  beginRequest,
  endRequest,
  STALL_RECONNECTING_MS,
  STALL_UNAVAILABLE_MS,
} from '@/lib/backend-status'

describe('backend-status store (stall-based)', () => {
  let open: number[] = []

  beforeEach(() => {
    jest.useFakeTimers()
    open = []
  })

  afterEach(() => {
    // Settle anything still in flight so the module singleton resets to online.
    open.forEach((id) => endRequest(id))
    jest.clearAllTimers()
    jest.useRealTimers()
  })

  it('starts online', () => {
    expect(getBackendStatus()).toBe('online')
  })

  it('stays online while a read is young, then escalates as it stalls', async () => {
    open.push(beginRequest())
    expect(getBackendStatus()).toBe('online')

    await jest.advanceTimersByTimeAsync(STALL_RECONNECTING_MS - 1000)
    expect(getBackendStatus()).toBe('online')

    await jest.advanceTimersByTimeAsync(1000) // cross the reconnecting threshold
    expect(getBackendStatus()).toBe('reconnecting')

    await jest.advanceTimersByTimeAsync(STALL_UNAVAILABLE_MS - STALL_RECONNECTING_MS)
    expect(getBackendStatus()).toBe('unavailable')
  })

  it('returns to online the moment the stalled read settles', async () => {
    const id = beginRequest()
    open.push(id)
    await jest.advanceTimersByTimeAsync(STALL_UNAVAILABLE_MS + 100)
    expect(getBackendStatus()).toBe('unavailable')

    endRequest(id)
    open = []
    expect(getBackendStatus()).toBe('online')
  })

  it('a read that completes quickly never surfaces the banner', async () => {
    const id = beginRequest()
    await jest.advanceTimersByTimeAsync(3000) // 3s — normal-slow, not a stall
    endRequest(id)
    expect(getBackendStatus()).toBe('online')

    // ...and nothing appears later either (nothing is in flight).
    await jest.advanceTimersByTimeAsync(STALL_UNAVAILABLE_MS)
    expect(getBackendStatus()).toBe('online')
  })

  it('tracks the OLDEST outstanding read, not the newest', async () => {
    const a = beginRequest()
    open.push(a)
    await jest.advanceTimersByTimeAsync(STALL_RECONNECTING_MS)
    expect(getBackendStatus()).toBe('reconnecting')

    const b = beginRequest() // fresh read starts while `a` is still stalled
    open.push(b)
    expect(getBackendStatus()).toBe('reconnecting')

    endRequest(b) // settling the young one doesn't clear the old stall
    open = [a]
    expect(getBackendStatus()).toBe('reconnecting')

    endRequest(a)
    open = []
    expect(getBackendStatus()).toBe('online')
  })
})
