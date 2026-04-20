/**
 * @jest-environment jsdom
 */
import { collectContext, reportClientError, __resetForTest } from '@/lib/crash-reporter'

describe('crash-reporter.collectContext', () => {
  beforeEach(() => {
    __resetForTest()
  })

  it('sanitizes url by dropping query + fragment', () => {
    const ctx = collectContext({
      href: 'https://gen.nomadkaraoke.com/en/app/jobs/?token=abc#/x/review',
      userAgent: 'Mozilla/5.0 Firefox/138.0',
    })
    expect(ctx.url).toBe('https://gen.nomadkaraoke.com/en/app/jobs/')
  })

  it('captures viewport dimensions', () => {
    const ctx = collectContext({
      href: 'https://x.example/',
      userAgent: 'ua',
      innerWidth: 412,
      innerHeight: 915,
    })
    expect(ctx.viewport).toEqual({ w: 412, h: 915 })
  })
})

describe('crash-reporter.reportClientError', () => {
  const fetchMock = jest.fn()

  beforeEach(() => {
    __resetForTest()
    fetchMock.mockReset()
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ pattern_id: 'p', is_new: true }) })
    ;(global as any).fetch = fetchMock
  })

  it('POSTs sanitized payload to /api/client-errors', async () => {
    await reportClientError({
      error: new TypeError('boom'),
      source: 'test',
      context: { href: 'https://gen.nomadkaraoke.com/en/app/', userAgent: 'ua' },
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toMatch(/\/api\/client-errors$/)
    expect(init.method).toBe('POST')
    expect(init.keepalive).toBe(true)
    const body = JSON.parse(init.body)
    expect(body.message).toBe('TypeError: boom')
    expect(body.stack).toContain('TypeError')
    expect(body.source).toBe('test')
    expect(body.url).toBe('https://gen.nomadkaraoke.com/en/app/')
  })

  it('dedupes identical errors within the window', async () => {
    const err = new Error('same')
    await reportClientError({ error: err, source: 'a', context: { href: '', userAgent: '' } })
    await reportClientError({ error: err, source: 'a', context: { href: '', userAgent: '' } })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('does not throw on fetch failure', async () => {
    fetchMock.mockRejectedValueOnce(new Error('offline'))
    await expect(
      reportClientError({
        error: new Error('x'),
        source: 'test',
        context: { href: '', userAgent: '' },
      })
    ).resolves.toBeUndefined()
  })
})
