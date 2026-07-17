/**
 * @jest-environment jsdom
 */
import {
  isStale,
  hardReload,
  isChunkLoadError,
  __resetForTest,
  __setBuildShaForTest,
} from '@/lib/version-check'

describe('version-check.isChunkLoadError', () => {
  it('matches webpack ChunkLoadError by name', () => {
    const e = new Error('boom')
    e.name = 'ChunkLoadError'
    expect(isChunkLoadError(e)).toBe(true)
  })

  it('matches webpack "Loading chunk <id> failed" message', () => {
    expect(isChunkLoadError(new Error('Loading chunk 429 failed'))).toBe(true)
  })

  it.each([
    // Turbopack (Next.js 16 default) — a plain Error, no ChunkLoadError name.
    'Failed to load chunk /_next/static/chunks/e66613528f386db8.js from module 64893',
    'Failed to load chunk /_next/static/chunks/abc.js as a runtime dependency of chunk 12',
    'Failed to load chunk /_next/static/chunks/abc.js from an HMR update',
  ])('matches Turbopack chunk-load message: %s', (msg) => {
    expect(isChunkLoadError(new Error(msg))).toBe(true)
  })

  it('matches native ESM dynamic-import failures', () => {
    expect(
      isChunkLoadError(new Error('Failed to fetch dynamically imported module: https://x/y.js')),
    ).toBe(true)
    expect(isChunkLoadError(new Error('Importing a module script failed.'))).toBe(true)
  })

  it('ignores unrelated errors and falsy input', () => {
    expect(isChunkLoadError(new Error('Cannot read properties of undefined'))).toBe(false)
    expect(isChunkLoadError(null)).toBe(false)
    expect(isChunkLoadError(undefined)).toBe(false)
  })
})

describe('version-check.isStale', () => {
  const fetchMock = jest.fn()
  beforeEach(() => {
    __resetForTest()
    __setBuildShaForTest('aaa')
    fetchMock.mockReset()
    ;(global as any).fetch = fetchMock
  })

  it('returns stale=false when SHAs match', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ build_sha: 'aaa' }) })
    const r = await isStale()
    expect(r.stale).toBe(false)
    expect(r.latestSha).toBe('aaa')
  })

  it('returns stale=true when SHAs differ', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ build_sha: 'bbb' }) })
    const r = await isStale()
    expect(r.stale).toBe(true)
    expect(r.latestSha).toBe('bbb')
  })

  it('fetches with cache: no-store', async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ build_sha: 'aaa' }) })
    await isStale()
    const [, init] = fetchMock.mock.calls[0]
    expect(init.cache).toBe('no-store')
  })

  it('treats fetch failures as not-stale (fail-safe)', async () => {
    fetchMock.mockRejectedValue(new Error('offline'))
    const r = await isStale()
    expect(r.stale).toBe(false)
  })

  it('treats "dev" build_sha as not-stale (local dev)', async () => {
    __setBuildShaForTest('dev')
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ build_sha: 'bbb' }) })
    const r = await isStale()
    expect(r.stale).toBe(false)
  })
})

describe('version-check.hardReload circuit breaker', () => {
  const assignMock = jest.fn()
  let originalLocation: Location

  beforeEach(() => {
    __resetForTest()
    assignMock.mockReset()
    originalLocation = window.location
    // @ts-expect-error — overwriting readonly for test
    delete window.location
    // @ts-expect-error — mock location
    window.location = {
      pathname: '/en/app/jobs/',
      assign: assignMock,
      reload: jest.fn(),
    }
  })

  afterEach(() => {
    // @ts-expect-error — restore
    window.location = originalLocation
  })

  it('navigates with _v cache-buster on first call', () => {
    hardReload('bbb')
    expect(assignMock).toHaveBeenCalledTimes(1)
    expect(assignMock.mock.calls[0][0]).toContain('_v=bbb')
    expect(assignMock.mock.calls[0][0]).toContain('/en/app/jobs/')
  })

  it('refuses to reload twice within 60s (circuit breaker)', () => {
    hardReload('bbb')
    hardReload('ccc')
    expect(assignMock).toHaveBeenCalledTimes(1)
  })
})
