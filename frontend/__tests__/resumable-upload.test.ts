/**
 * @jest-environment jsdom
 *
 * Tests for the GCS resumable upload engine.
 *
 * XHR is replaced with a scripted mock so we can assert the exact
 * Content-Range choreography: offset query → chunked PUTs → resume-from-offset
 * after failures, with no persisted byte ever re-sent.
 */
import {
  uploadResumable,
  queryUploadOffset,
  CHUNK_ALIGNMENT,
  ResumableUploadError,
} from '@/lib/resumable-upload'

interface Scripted {
  status?: number
  range?: string
  progress?: number[]
  networkError?: boolean
}

interface RecordedRequest {
  method: string
  url: string
  headers: Record<string, string>
  bodySize: number
}

class MockXHR {
  static queue: Scripted[] = []
  static requests: RecordedRequest[] = []

  method = ''
  url = ''
  status = 0
  upload: { onprogress: ((e: { lengthComputable: boolean; loaded: number }) => void) | null } = { onprogress: null }
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  onabort: (() => void) | null = null
  private headers: Record<string, string> = {}
  private responseHeaders: Record<string, string> = {}
  private aborted = false

  open(method: string, url: string) {
    this.method = method
    this.url = url
  }
  setRequestHeader(k: string, v: string) {
    this.headers[k] = v
  }
  getResponseHeader(k: string): string | null {
    return this.responseHeaders[k] ?? null
  }
  abort() {
    this.aborted = true
    setTimeout(() => this.onabort?.(), 0)
  }
  send(body?: Blob) {
    MockXHR.requests.push({
      method: this.method,
      url: this.url,
      headers: { ...this.headers },
      bodySize: body ? (body as Blob).size : 0,
    })
    const script = MockXHR.queue.shift() ?? { status: 200 }
    setTimeout(() => {
      if (this.aborted) return
      for (const loaded of script.progress ?? []) {
        this.upload.onprogress?.({ lengthComputable: true, loaded })
      }
      if (script.networkError) {
        this.onerror?.()
        return
      }
      this.status = script.status ?? 200
      if (script.range) this.responseHeaders['Range'] = script.range
      this.onload?.()
    }, 0)
  }
}

const realXHR = global.XMLHttpRequest

beforeEach(() => {
  MockXHR.queue = []
  MockXHR.requests = []
  ;(global as any).XMLHttpRequest = MockXHR
})

afterEach(() => {
  ;(global as any).XMLHttpRequest = realXHR
})

function makeFile(size: number): File {
  return new File([new ArrayBuffer(size)], 'track.mp3')
}

const SESSION = 'https://storage.googleapis.com/upload/session-abc'
// Fast options for tests: minimal chunk, near-instant backoff.
const OPTS = { chunkSize: CHUNK_ALIGNMENT, baseRetryDelayMs: 1 }

describe('uploadResumable — chunk choreography', () => {
  it('queries offset then uploads aligned chunks with correct Content-Range', async () => {
    const size = CHUNK_ALIGNMENT + 5000 // one full chunk + a final partial
    MockXHR.queue = [
      { status: 308 }, // offset query: fresh session, nothing persisted
      { status: 308, range: `bytes=0-${CHUNK_ALIGNMENT - 1}` }, // chunk 1 ack
      { status: 200 }, // final chunk → complete
    ]

    await uploadResumable(SESSION, makeFile(size), OPTS)

    const [query, chunk1, chunk2] = MockXHR.requests
    expect(query.headers['Content-Range']).toBe(`bytes */${size}`)
    expect(query.bodySize).toBe(0)
    expect(chunk1.headers['Content-Range']).toBe(`bytes 0-${CHUNK_ALIGNMENT - 1}/${size}`)
    expect(chunk1.bodySize).toBe(CHUNK_ALIGNMENT)
    expect(chunk2.headers['Content-Range']).toBe(`bytes ${CHUNK_ALIGNMENT}-${size - 1}/${size}`)
    expect(chunk2.bodySize).toBe(5000)
  })

  it('resumes from the offset the session reports (no persisted bytes re-sent)', async () => {
    const size = 3 * CHUNK_ALIGNMENT
    const persisted = 2 * CHUNK_ALIGNMENT
    MockXHR.queue = [
      { status: 308, range: `bytes=0-${persisted - 1}` }, // session already has 2 chunks
      { status: 200 }, // remaining chunk completes the object
    ]

    await uploadResumable(SESSION, makeFile(size), OPTS)

    expect(MockXHR.requests).toHaveLength(2)
    const chunk = MockXHR.requests[1]
    expect(chunk.headers['Content-Range']).toBe(`bytes ${persisted}-${size - 1}/${size}`)
    expect(chunk.bodySize).toBe(size - persisted)
  })

  it('resolves without uploading when the session reports complete', async () => {
    MockXHR.queue = [{ status: 200 }] // offset query says done
    await uploadResumable(SESSION, makeFile(1000), OPTS)
    expect(MockXHR.requests).toHaveLength(1)
  })
})

describe('uploadResumable — failure recovery', () => {
  it('recovers the true offset after a network failure and resumes there', async () => {
    const size = 2 * CHUNK_ALIGNMENT
    MockXHR.queue = [
      { status: 308 }, // initial offset query: 0
      { networkError: true }, // chunk 1 dies mid-flight
      { status: 308, range: `bytes=0-${CHUNK_ALIGNMENT - 1}` }, // recovery query: GCS kept chunk 1!
      { status: 200 }, // only the remainder is sent
    ]

    await uploadResumable(SESSION, makeFile(size), OPTS)

    expect(MockXHR.requests).toHaveLength(4)
    const retried = MockXHR.requests[3]
    // Resumes at the recovered offset — the persisted chunk is never re-sent.
    expect(retried.headers['Content-Range']).toBe(`bytes ${CHUNK_ALIGNMENT}-${size - 1}/${size}`)
    expect(retried.bodySize).toBe(CHUNK_ALIGNMENT)
  })

  it('discovers completion during failure recovery', async () => {
    const size = CHUNK_ALIGNMENT
    MockXHR.queue = [
      { status: 308 },
      { networkError: true }, // chunk dies... but GCS actually persisted it all
      { status: 200 }, // recovery query: complete
    ]
    await uploadResumable(SESSION, makeFile(size), OPTS)
    expect(MockXHR.requests).toHaveLength(3)
  })

  it('throws immediately on a permanent error (session expired)', async () => {
    MockXHR.queue = [
      { status: 308 },
      { status: 410 }, // session gone — retrying is pointless
    ]
    await expect(uploadResumable(SESSION, makeFile(1000), OPTS)).rejects.toMatchObject({
      name: 'ResumableUploadError',
      status: 410,
      permanent: true,
    })
    expect(MockXHR.requests).toHaveLength(2) // no retries
  })

  it('gives up after maxConsecutiveFailures transient failures', async () => {
    // Every chunk attempt and every recovery query fails.
    MockXHR.queue = [{ status: 308 }]
    for (let i = 0; i < 20; i++) MockXHR.queue.push({ networkError: true })

    await expect(
      uploadResumable(SESSION, makeFile(CHUNK_ALIGNMENT), { ...OPTS, maxConsecutiveFailures: 2 }),
    ).rejects.toMatchObject({ name: 'ResumableUploadError', permanent: false })
  })

  it('reports progress with loaded bytes and state transitions', async () => {
    const size = CHUNK_ALIGNMENT
    MockXHR.queue = [
      { status: 308 },
      { status: 200, progress: [100_000, size] },
    ]
    const states: string[] = []
    let lastLoaded = 0
    await uploadResumable(SESSION, makeFile(size), {
      ...OPTS,
      onProgress: (p) => {
        states.push(p.state)
        expect(p.loaded).toBeGreaterThanOrEqual(lastLoaded)
        lastLoaded = p.loaded
        expect(p.total).toBe(size)
      },
    })
    expect(lastLoaded).toBe(size)
    expect(states).toContain('uploading')
  })
})

describe('queryUploadOffset', () => {
  it('parses the Range header into the next offset', async () => {
    MockXHR.queue = [{ status: 308, range: 'bytes=0-1048575' }]
    await expect(queryUploadOffset(SESSION, 5_000_000)).resolves.toBe(1_048_576)
  })

  it('returns 0 for a fresh session with no Range header', async () => {
    MockXHR.queue = [{ status: 308 }]
    await expect(queryUploadOffset(SESSION, 5_000_000)).resolves.toBe(0)
  })

  it('returns "complete" when the object is finished', async () => {
    MockXHR.queue = [{ status: 201 }]
    await expect(queryUploadOffset(SESSION, 5_000_000)).resolves.toBe('complete')
  })
})
