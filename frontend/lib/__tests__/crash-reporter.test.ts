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
  beforeEach(() => {
    __resetForTest()
    global.fetch = jest.fn().mockResolvedValue({ ok: true }) as unknown as typeof fetch
  })
  afterEach(() => jest.restoreAllMocks())

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
    expect(global.fetch).not.toHaveBeenCalled()
  })

  it('POSTs a genuine error', async () => {
    await reportClientError({
      error: new Error('boom'),
      source: 'window.onerror',
      context: ctx,
    })
    expect(global.fetch).toHaveBeenCalledTimes(1)
  })
})
