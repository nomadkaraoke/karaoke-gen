/**
 * @jest-environment jsdom
 */
import { addBreadcrumb, __resetDiagnosticsForTest } from '@/lib/diagnostics'
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
    __resetDiagnosticsForTest()
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
  const bodyOf = (call = 0) => JSON.parse(fetchSpy.mock.calls[call][1].body)

  it('attaches diagnostics and the breadcrumb trail', async () => {
    addBreadcrumb('upload', 'uploading file 2/2 (88 MB)')
    await reportClientError({ error: new Error('boom'), source: 'window.onerror', context: ctx, extra: { lineno: 3 } })
    const body = bodyOf()
    expect(body.extra.lineno).toBe(3)
    expect(body.extra.diagnostics).toEqual(expect.objectContaining({
      page_age_s: expect.any(Number), dom_nodes: expect.any(Number), live_blob_urls: expect.any(Number), reports_this_page: 1,
    }))
    expect(body.extra.breadcrumbs).toEqual(
      expect.arrayContaining([expect.objectContaining({ category: 'upload', message: 'uploading file 2/2 (88 MB)' })])
    )
    expect(body.extra.synthetic_error).toBeUndefined()
  })

  it('drops the reporter-made stack of a synthetic error and flags it', async () => {
    await reportClientError({ error: new Error('out of memory'), source: 'window.onerror', context: ctx, synthetic: true })
    const body = bodyOf()
    expect(body.message).toBe('Error: out of memory')
    expect(body.stack).toBeNull()
    expect(body.extra.synthetic_error).toBe(true)
  })

  it('caps how many reports one page load can send', async () => {
    for (let i = 0; i < 30; i++) {
      await reportClientError({ error: new Error(`distinct ${i}`), source: 'window.onerror', context: ctx })
    }
    expect(fetchSpy).toHaveBeenCalledTimes(20)
  })
})
