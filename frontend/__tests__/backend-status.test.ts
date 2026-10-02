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
  configurePrewarm,
  markBackendReachable,
  prewarmBackend,
  installBackendPrewarm,
  __resetBackendStatusForTest,
  STALL_RECONNECTING_MS,
  STALL_UNAVAILABLE_MS,
  PROBE_FRESH_MS,
  REACHABLE_FRESH_MS,
  WAKING_SHOW_MS,
  WAKING_ESCALATE_MS,
  COLD_PROBE_AFTER_MS,
  PREWARM_AFTER_HIDDEN_MS,
} from '@/lib/backend-status'

describe('backend-status store (stall-based)', () => {
  let open: number[] = []

  beforeEach(() => {
    jest.useFakeTimers()
    open = []
    // These suites cover a WARM session (the backend answered moments ago), so
    // a stall is a recycle/outage rather than a cold start.
    __resetBackendStatusForTest()
    markBackendReachable()
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
    // These suites cover a WARM session (the backend answered moments ago), so
    // a stall is a recycle/outage rather than a cold start.
    __resetBackendStatusForTest()
    markBackendReachable()
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

describe('backend-status store (cold start / waking)', () => {
  let open: number[] = []

  beforeEach(() => {
    jest.useFakeTimers()
    open = []
    __resetBackendStatusForTest() // fresh page load: nothing has answered yet
  })

  afterEach(() => {
    open.forEach((id) => endRequest(id))
    __resetBackendStatusForTest()
    jest.clearAllTimers()
    jest.useRealTimers()
  })

  /** A probe that hangs like a cold instance and fails via its 4s timeout. */
  const coldProbe = () =>
    jest.fn(() => new Promise<boolean>((res) => setTimeout(() => res(false), 4000)))

  it('cold start: shows the calm waking state within seconds, then clears when the read completes', async () => {
    const probe = coldProbe()
    configureHealthProbe(probe)

    const id = beginRequest()
    open.push(id)
    expect(probe).not.toHaveBeenCalled() // a normal fast load never probes
    await jest.advanceTimersByTimeAsync(COLD_PROBE_AFTER_MS)
    expect(probe).toHaveBeenCalledTimes(1) // probed at 1s, not at 10s
    expect(getBackendStatus()).toBe('online')

    await jest.advanceTimersByTimeAsync(WAKING_SHOW_MS)
    expect(getBackendStatus()).toBe('online') // probe has no verdict yet (t=4s)

    await jest.advanceTimersByTimeAsync(1000) // t=5s: probe failed at its 4s timeout
    expect(getBackendStatus()).toBe('waking')

    // Through a typical ~18s cold start it stays calm — never the warning.
    await jest.advanceTimersByTimeAsync(13_000) // t=18s
    expect(getBackendStatus()).toBe('waking')

    markBackendReachable()
    endRequest(id)
    open = []
    expect(getBackendStatus()).toBe('online')
  })

  it('cold start: a read that answers quickly never shows anything', async () => {
    configureHealthProbe(coldProbe())
    const id = beginRequest()
    await jest.advanceTimersByTimeAsync(2000)
    markBackendReachable()
    endRequest(id)
    expect(getBackendStatus()).toBe('online')
  })

  it('cold start: a warm backend with a merely slow endpoint shows nothing (probe OK)', async () => {
    configureHealthProbe(() => Promise.resolve(true))
    open.push(beginRequest())
    await jest.advanceTimersByTimeAsync(STALL_UNAVAILABLE_MS + 5000)
    expect(getBackendStatus()).toBe('online')
  })

  it('cold start that drags on past WAKING_ESCALATE_MS escalates to the normal banner', async () => {
    configureHealthProbe(coldProbe())
    open.push(beginRequest())

    await jest.advanceTimersByTimeAsync(WAKING_ESCALATE_MS - 1000)
    expect(getBackendStatus()).toBe('waking')

    await jest.advanceTimersByTimeAsync(2000)
    // Many consecutive probe failures by now → straight to the full message.
    expect(getBackendStatus()).toBe('unavailable')
  })

  it('escalation is measured from the start of the cold episode, so a long outage never flaps back to waking', async () => {
    configureHealthProbe(coldProbe())
    const first = beginRequest()
    open.push(first)
    await jest.advanceTimersByTimeAsync(30_000)
    open.push(beginRequest()) // e.g. a polling read starting mid-episode
    await jest.advanceTimersByTimeAsync(31_000) // t=61s
    expect(getBackendStatus()).toBe('unavailable')

    // The first read times out; the newer one (only 31s old) is now the oldest.
    endRequest(first)
    open = open.filter((id) => id !== first)
    await jest.advanceTimersByTimeAsync(1000)
    expect(getBackendStatus()).toBe('unavailable') // not back to 'waking'
  })

  it('a stale failed-probe verdict from before the backend answered cannot trigger waking', async () => {
    // An earlier episode leaves a failed verdict behind...
    configureHealthProbe(() => Promise.resolve(false))
    const old = beginRequest()
    await jest.advanceTimersByTimeAsync(6000)
    expect(getBackendStatus()).toBe('waking')
    markBackendReachable()
    endRequest(old)
    expect(getBackendStatusDebug().lastProbeOk).toBeNull() // verdict dropped

    // ...much later (backend idle-timeout window passed, but kept warm by others),
    // a read that's merely slow: the fresh probe answers OK → nothing shown.
    configureHealthProbe(() => new Promise<boolean>((res) => setTimeout(() => res(true), 2500)))
    markBackendReachable()
    await jest.advanceTimersByTimeAsync(REACHABLE_FRESH_MS + 1000)
    open.push(beginRequest())
    await jest.advanceTimersByTimeAsync(3500) // past WAKING_SHOW_MS, probe still pending
    expect(getBackendStatus()).toBe('online')
    await jest.advanceTimersByTimeAsync(5000)
    expect(getBackendStatus()).toBe('online')
  })

  it('previously reachable, then a stall → normal reconnecting banner (recycle, not a cold start)', async () => {
    configureHealthProbe(coldProbe())
    markBackendReachable()
    await jest.advanceTimersByTimeAsync(30_000) // backend answered 30s ago

    open.push(beginRequest())
    await jest.advanceTimersByTimeAsync(8000)
    expect(getBackendStatus()).toBe('online') // no waking state for a recycle
    await jest.advanceTimersByTimeAsync(STALL_RECONNECTING_MS) // probe at 10s, fails at 14s
    expect(getBackendStatus()).toBe('reconnecting')
  })

  it('reachable long ago (backend idled to zero) → a new stall is treated as a cold start', async () => {
    configureHealthProbe(coldProbe())
    markBackendReachable()
    await jest.advanceTimersByTimeAsync(REACHABLE_FRESH_MS + 1000)

    open.push(beginRequest())
    await jest.advanceTimersByTimeAsync(6000)
    expect(getBackendStatus()).toBe('waking')
  })

  it('without a probe (pure stall mode) a cold stall still shows waking, not the warning', async () => {
    open.push(beginRequest())
    await jest.advanceTimersByTimeAsync(WAKING_SHOW_MS)
    expect(getBackendStatus()).toBe('waking')
    await jest.advanceTimersByTimeAsync(STALL_UNAVAILABLE_MS)
    expect(getBackendStatus()).toBe('waking')
  })

  it('exposes time since the backend last answered for telemetry', async () => {
    expect(getBackendStatusDebug().sinceReachableMs).toBeNull()
    markBackendReachable()
    await jest.advanceTimersByTimeAsync(1500)
    expect(getBackendStatusDebug().sinceReachableMs).toBe(1500)
  })
})

describe('backend-status prewarm', () => {
  beforeEach(() => {
    jest.useFakeTimers()
    __resetBackendStatusForTest()
  })

  afterEach(() => {
    configurePrewarm(null)
    __resetBackendStatusForTest()
    jest.useRealTimers()
  })

  it('installBackendPrewarm registers the passed fetch when api.ts has not', async () => {
    configurePrewarm(null)
    const warm = jest.fn(() => Promise.resolve(true))
    installBackendPrewarm(warm)
    await jest.advanceTimersByTimeAsync(0)
    expect(warm).toHaveBeenCalledTimes(1)
    expect(getBackendStatusDebug().sinceReachableMs).toBe(0)
  })

  it('fires once while in flight and marks the backend reachable on success', async () => {
    let finish!: (ok: boolean) => void
    const warm = jest.fn(() => new Promise<boolean>((res) => (finish = res)))
    configurePrewarm(warm)

    prewarmBackend()
    prewarmBackend() // de-duped while the first is in flight
    expect(warm).toHaveBeenCalledTimes(1)
    expect(getBackendStatusDebug().sinceReachableMs).toBeNull()

    finish(true)
    await jest.advanceTimersByTimeAsync(0)
    expect(getBackendStatusDebug().sinceReachableMs).toBe(0)

    // Recently reachable → no need to warm again.
    prewarmBackend()
    expect(warm).toHaveBeenCalledTimes(1)
  })

  it('a failed prewarm does not mark the backend reachable and can be retried', async () => {
    const warm = jest.fn(() => Promise.resolve(false))
    configurePrewarm(warm)
    prewarmBackend()
    await jest.advanceTimersByTimeAsync(0)
    expect(getBackendStatusDebug().sinceReachableMs).toBeNull()
    prewarmBackend()
    expect(warm).toHaveBeenCalledTimes(2)
  })

  it('re-warms when the tab returns after a long absence, but not after a short one', async () => {
    const warm = jest.fn(() => Promise.resolve(true))
    configurePrewarm(warm)
    let visibility: DocumentVisibilityState = 'visible'
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => visibility,
    })
    const setVisibility = (v: DocumentVisibilityState) => {
      visibility = v
      document.dispatchEvent(new Event('visibilitychange'))
    }

    installBackendPrewarm()
    await jest.advanceTimersByTimeAsync(0)
    expect(warm).toHaveBeenCalledTimes(1)

    // Short absence: no re-warm.
    setVisibility('hidden')
    await jest.advanceTimersByTimeAsync(60_000)
    setVisibility('visible')
    expect(warm).toHaveBeenCalledTimes(1)

    // Long absence (backend may have scaled to zero): re-warm.
    setVisibility('hidden')
    await jest.advanceTimersByTimeAsync(PREWARM_AFTER_HIDDEN_MS + 1000)
    setVisibility('visible')
    expect(warm).toHaveBeenCalledTimes(2)
  })
})
