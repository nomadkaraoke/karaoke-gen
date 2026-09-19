/**
 * @jest-environment jsdom
 *
 * Tests for the backend-connectivity status store (lib/backend-status.ts).
 * Status is derived from how long the OLDEST in-flight tracked read has been
 * outstanding — a read that completes promptly never surfaces the banner.
 */

import {
  getBackendStatus,
  getBackendStatusDebug,
  beginRequest,
  endRequest,
  configureHealthProbe,
  STALL_RECONNECTING_MS,
  STALL_UNAVAILABLE_MS,
  PROBE_FRESH_MS,
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

describe('backend-status store (health-probe confirmation)', () => {
  let open: number[] = []

  beforeEach(() => {
    jest.useFakeTimers()
    open = []
  })

  afterEach(() => {
    open.forEach((id) => endRequest(id))
    // Restore pure stall-based behavior so other suites are unaffected.
    configureHealthProbe(null)
    jest.clearAllTimers()
    jest.useRealTimers()
  })

  it('suppresses the banner while the probe reports the backend reachable', async () => {
    const probe = jest.fn(() => Promise.resolve(true))
    configureHealthProbe(probe)

    open.push(beginRequest())
    await jest.advanceTimersByTimeAsync(STALL_UNAVAILABLE_MS + 5000)
    expect(getBackendStatus()).toBe('online')
    expect(probe).toHaveBeenCalled()
  })

  it('shows the banner once the probe confirms the backend is unreachable', async () => {
    configureHealthProbe(() => Promise.resolve(false))

    open.push(beginRequest())
    await jest.advanceTimersByTimeAsync(STALL_RECONNECTING_MS)
    expect(getBackendStatus()).toBe('reconnecting')

    await jest.advanceTimersByTimeAsync(STALL_UNAVAILABLE_MS - STALL_RECONNECTING_MS)
    expect(getBackendStatus()).toBe('unavailable')
  })

  it('re-probes as the verdict goes stale, and clears the banner if the backend recovers', async () => {
    let reachable = false
    const probe = jest.fn(() => Promise.resolve(reachable))
    configureHealthProbe(probe)

    open.push(beginRequest())
    await jest.advanceTimersByTimeAsync(STALL_UNAVAILABLE_MS)
    expect(getBackendStatus()).toBe('unavailable')

    // Backend comes back (even though the old read is still hung) — the next
    // re-probe succeeds and the banner clears.
    reachable = true
    await jest.advanceTimersByTimeAsync(PROBE_FRESH_MS + 2000)
    expect(getBackendStatus()).toBe('online')
    expect(probe.mock.calls.length).toBeGreaterThan(1)
  })

  it('holds the banner back while the probe has no verdict yet', async () => {
    // A probe that takes 3s to fail (e.g. its own timeout racing a hung origin).
    configureHealthProbe(
      () => new Promise((res) => setTimeout(() => res(false), 3000)),
    )

    open.push(beginRequest())
    await jest.advanceTimersByTimeAsync(STALL_RECONNECTING_MS)
    expect(getBackendStatus()).toBe('online') // stalled, but not yet confirmed

    await jest.advanceTimersByTimeAsync(3000) // verdict lands
    expect(getBackendStatus()).toBe('reconnecting')
  })

  it('needs TWO consecutive failed probes before escalating to unavailable', async () => {
    // Each probe takes 3s to fail — realistic for a probe racing its own
    // timeout against a briefly-pegged (not down) instance.
    const probe = jest.fn(
      () => new Promise<boolean>((res) => setTimeout(() => res(false), 3000)),
    )
    configureHealthProbe(probe)

    open.push(beginRequest())
    // t=10s: probe #1 starts. t=13s: failure #1 recorded.
    // t=20s: the stall alone now qualifies for "unavailable", but only one
    //        probe failure has been recorded (verdict fresh until t=23s) —
    //        the calm reconnecting pill must be all the user sees.
    await jest.advanceTimersByTimeAsync(STALL_UNAVAILABLE_MS + 1000) // t=21s
    expect(getBackendStatus()).toBe('reconnecting')
    expect(getBackendStatusDebug().consecutiveProbeFailures).toBe(1)

    // t=23s: verdict stale → probe #2 starts. t=26s: failure #2 → escalate.
    await jest.advanceTimersByTimeAsync(6000) // t=27s
    expect(getBackendStatus()).toBe('unavailable')
    expect(getBackendStatusDebug().consecutiveProbeFailures).toBe(2)
  })

  it('a probe success resets the consecutive-failure count', async () => {
    let reachable = false
    const probe = jest.fn(() => Promise.resolve(reachable))
    configureHealthProbe(probe)

    open.push(beginRequest())
    await jest.advanceTimersByTimeAsync(STALL_UNAVAILABLE_MS + PROBE_FRESH_MS)
    expect(getBackendStatusDebug().consecutiveProbeFailures).toBeGreaterThanOrEqual(2)

    reachable = true
    await jest.advanceTimersByTimeAsync(PROBE_FRESH_MS + 2000)
    expect(getBackendStatus()).toBe('online')
    expect(getBackendStatusDebug().consecutiveProbeFailures).toBe(0)
  })

  it('exposes a diagnostic snapshot for telemetry', async () => {
    configureHealthProbe(() => Promise.resolve(false))
    open.push(beginRequest())
    await jest.advanceTimersByTimeAsync(STALL_RECONNECTING_MS + 1000)

    const debug = getBackendStatusDebug()
    expect(debug.inFlightCount).toBe(1)
    expect(debug.oldestStallMs).toBeGreaterThanOrEqual(STALL_RECONNECTING_MS)
    expect(debug.lastProbeOk).toBe(false)
  })
})
