import { generateCustomLyrics } from '../customLyrics'

// jsdom does not expose `Response` as a global. Provide a minimal polyfill
// sufficient for what the API client uses: status / ok / json().
class MockResponse {
  readonly status: number
  readonly ok: boolean
  private readonly _body: string
  constructor(body: string, init: { status?: number; headers?: Record<string, string> } = {}) {
    this._body = body
    this.status = init.status ?? 200
    this.ok = this.status >= 200 && this.status < 300
  }
  async json() {
    return JSON.parse(this._body)
  }
}
;(globalThis as unknown as { Response: typeof MockResponse }).Response =
  (globalThis as unknown as { Response?: typeof MockResponse }).Response ?? MockResponse

describe('generateCustomLyrics', () => {
  let originalFetch: typeof fetch

  beforeEach(() => {
    originalFetch = global.fetch
  })

  afterEach(() => {
    global.fetch = originalFetch
  })

  it('posts multipart form-data with text input', async () => {
    const captured: { url: string; init: RequestInit | undefined } = { url: '', init: undefined }
    global.fetch = jest.fn(async (url: RequestInfo, init?: RequestInit) => {
      captured.url = String(url)
      captured.init = init
      return new Response(
        JSON.stringify({
          lines: ['a', 'b'],
          warnings: [],
          model: 'gemini-3.1-pro-preview',
          line_count_mismatch: false,
          retry_count: 0,
          duration_ms: 50,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      )
    }) as unknown as typeof fetch

    const result = await generateCustomLyrics(
      'job-123',
      {
        existingLines: ['one', 'two'],
        customText: 'make it about cats',
        notes: 'for clara',
        artist: 'A',
        title: 'T',
      },
      undefined,
      'fake-token',
    )

    expect(captured.url).toContain('/api/review/job-123/custom-lyrics/generate')
    const init = captured.init!
    expect(init.method).toBe('POST')
    const body = init.body as FormData
    expect(body.get('existing_lines')).toBe(JSON.stringify(['one', 'two']))
    expect(body.get('custom_text')).toBe('make it about cats')
    expect(body.get('notes')).toBe('for clara')
    expect((init.headers as Record<string, string>).Authorization).toBe(
      'Bearer fake-token',
    )
    expect(result.lines).toEqual(['a', 'b'])
    expect(result.lineCountMismatch).toBe(false)
  })

  it('posts file when provided instead of text', async () => {
    const captured: { init: RequestInit | undefined } = { init: undefined }
    global.fetch = jest.fn(async (_url: RequestInfo, init?: RequestInit) => {
      captured.init = init
      return new Response(
        JSON.stringify({
          lines: ['x'],
          warnings: [],
          model: 'm',
          line_count_mismatch: false,
          retry_count: 0,
          duration_ms: 5,
        }),
        { status: 200 },
      )
    }) as unknown as typeof fetch

    const file = new File([new Uint8Array([1, 2, 3])], 'brief.docx', {
      type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    })

    await generateCustomLyrics(
      'job-1',
      { existingLines: ['a'], file },
      undefined,
      'tok',
    )

    const body = (captured.init!.body as FormData)
    expect(body.get('file')).toBeInstanceOf(File)
    expect(body.has('custom_text')).toBe(false)
  })

  it('throws CustomLyricsApiError on non-2xx', async () => {
    global.fetch = jest.fn(
      async () =>
        new Response(JSON.stringify({ detail: 'AI service unavailable' }), {
          status: 502,
          headers: { 'Content-Type': 'application/json' },
        }),
    ) as unknown as typeof fetch

    await expect(
      generateCustomLyrics(
        'job-1',
        { existingLines: ['a'], customText: 'x' },
        undefined,
        'tok',
      ),
    ).rejects.toMatchObject({ status: 502, message: expect.stringContaining('AI service') })
  })

  it('passes AbortSignal through to fetch', async () => {
    const captured: { init: RequestInit | undefined } = { init: undefined }
    global.fetch = jest.fn(async (_url: RequestInfo, init?: RequestInit) => {
      captured.init = init
      return new Response(JSON.stringify({
        lines: ['a'], warnings: [], model: 'm',
        line_count_mismatch: false, retry_count: 0, duration_ms: 1,
      }), { status: 200 })
    }) as unknown as typeof fetch

    const ac = new AbortController()
    await generateCustomLyrics(
      'job-1',
      { existingLines: ['a'], customText: 'x' },
      ac.signal,
      'tok',
    )
    expect(captured.init!.signal).toBe(ac.signal)
  })
})
