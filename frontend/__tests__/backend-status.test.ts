/**
 * @jest-environment jsdom
 *
 * Tests for the backend-connectivity status store (lib/backend-status.ts).
 */

import {
  getBackendStatus,
  reportBackendOnline,
  reportBackendTrouble,
  reportBackendUnavailable,
  UNAVAILABLE_AFTER_MS,
} from '@/lib/backend-status'

describe('backend-status store', () => {
  beforeEach(() => {
    // Reset the module singleton to a known-good baseline before each test.
    reportBackendOnline()
    jest.useFakeTimers()
  })

  afterEach(() => {
    jest.clearAllTimers()
    jest.useRealTimers()
  })

  it('starts online', () => {
    expect(getBackendStatus()).toBe('online')
  })

  it('moves to "reconnecting" on first trouble, then escalates to "unavailable" after the threshold', async () => {
    reportBackendTrouble()
    expect(getBackendStatus()).toBe('reconnecting')

    // Just before the threshold it should still be reconnecting...
    await jest.advanceTimersByTimeAsync(UNAVAILABLE_AFTER_MS - 1)
    expect(getBackendStatus()).toBe('reconnecting')

    // ...and cross it to become unavailable.
    await jest.advanceTimersByTimeAsync(2)
    expect(getBackendStatus()).toBe('unavailable')
  })

  it('does not reset the escalation clock on repeated trouble reports', async () => {
    reportBackendTrouble()
    await jest.advanceTimersByTimeAsync(UNAVAILABLE_AFTER_MS - 2000)
    // A second failure partway through must NOT push the deadline out.
    reportBackendTrouble()
    expect(getBackendStatus()).toBe('reconnecting')
    await jest.advanceTimersByTimeAsync(2001)
    expect(getBackendStatus()).toBe('unavailable')
  })

  it('reportBackendUnavailable escalates immediately without waiting', () => {
    reportBackendTrouble()
    reportBackendUnavailable()
    expect(getBackendStatus()).toBe('unavailable')
  })

  it('reportBackendOnline clears trouble and cancels a pending escalation', async () => {
    reportBackendTrouble()
    expect(getBackendStatus()).toBe('reconnecting')

    reportBackendOnline()
    expect(getBackendStatus()).toBe('online')

    // The previously-armed escalation timer must have been cancelled.
    await jest.advanceTimersByTimeAsync(UNAVAILABLE_AFTER_MS + 1000)
    expect(getBackendStatus()).toBe('online')
  })

  it('recovers to online from the unavailable state', () => {
    reportBackendUnavailable()
    expect(getBackendStatus()).toBe('unavailable')
    reportBackendOnline()
    expect(getBackendStatus()).toBe('online')
  })
})
