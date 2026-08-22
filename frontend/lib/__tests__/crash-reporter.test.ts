/**
 * @jest-environment jsdom
 */
import { isBenignError, reportClientError, __resetForTest } from '@/lib/crash-reporter'

describe('isBenignError', () => {
  it('treats ResizeObserver loop notices as benign (both phrasings)', () => {
    expect(
      isBenignError(new Error('ResizeObserver loop completed with undelivered notifications.'))
    ).toBe(true)
    expect(isBenignError(new Error('ResizeObserver loop limit exceeded'))).toBe(true)
    // window.onerror sometimes hands us the bare string.
    expect(isBenignError('ResizeObserver loop completed with undelivered notifications.')).toBe(
      true
    )
  })

  it('still treats media AbortError as benign', () => {
    const abort = new Error('The play() request was interrupted')
    abort.name = 'AbortError'
    expect(isBenignError(abort)).toBe(true)
  })

  it('does not suppress real errors', () => {
    expect(isBenignError(new Error('Cannot read properties of undefined'))).toBe(false)
    expect(
      isBenignError(new TypeError("Failed to set the 'currentTime' property"))
    ).toBe(false)
  })
})

describe('reportClientError', () => {
  // jsdom has no global fetch to spyOn, so assign directly — but save and
  // restore the original so the mock never leaks into other suites.
  let fetchSpy: jest.Mock
  let originalFetch: typeof global.fetch
  beforeEach(() => {
    __resetForTest()
    originalFetch = global.fetch
    fetchSpy = jest.fn().mockResolvedValue({ ok: true } as Response)
    global.fetch = fetchSpy as unknown as typeof fetch
  })
  afterEach(() => {
    global.fetch = originalFetch
    jest.restoreAllMocks()
  })

  const ctx = {
    href: 'https://gen.nomadkaraoke.com/en/app/jobs/',
    userAgent: 'test',
  }

  it('never POSTs a benign ResizeObserver error to the monitor', async () => {
    await reportClientError({
      error: new Error('ResizeObserver loop completed with undelivered notifications.'),
      source: 'window.onerror',
      context: ctx,
    })
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it('POSTs a genuine error', async () => {
    await reportClientError({
      error: new Error('boom'),
      source: 'window.onerror',
      context: ctx,
    })
    expect(fetchSpy).toHaveBeenCalledTimes(1)
  })
})
