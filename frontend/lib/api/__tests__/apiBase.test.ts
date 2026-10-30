/**
 * The AI review clients (auto-correct, custom lyrics) must pick their API base the same way as
 * lib/api.ts: a page served from a local host (the local stack, nomad-mobile's E2E in an emulator's
 * Chrome) talks to its own backend through relative URLs, never to production with its bearer token.
 */

const PROD = 'https://api.nomadkaraoke.com'

function setHostname(hostname: string) {
  delete (window as unknown as { location?: Location }).location
  Object.defineProperty(window, 'location', { value: { hostname, search: '', href: `http://${hostname}/`, pathname: '/' } as Location, configurable: true, writable: true })
}

async function capturePostedUrl(hostname: string, client: 'autoCorrect' | 'customLyrics'): Promise<string> {
  const original = window.location
  setHostname(hostname)
  let url = ''
  const originalFetch = global.fetch
  global.fetch = jest.fn(async (u: RequestInfo) => {
    url = String(u)
    return { ok: true, status: 200, json: async () => ({ suggestions: [], lines: [] }) } as unknown as Response
  }) as unknown as typeof fetch
  try {
    await jest.isolateModulesAsync(async () => {
      if (client === 'autoCorrect') {
        const mod = await import('../autoCorrect')
        await mod
          .fetchAutoCorrectSuggestions('job-1', {
            segments: [],
            referenceLyrics: {},
            settings: mod.DEFAULT_AUTO_CORRECT_SETTINGS,
          })
          .catch(() => {})
      } else {
        const mod = await import('../customLyrics')
        await mod
          .generateCustomLyrics('job-1', {
            existingLines: ['a'],
            customText: 'x',
            settings: mod.DEFAULT_GENERATION_SETTINGS,
          } as never)
          .catch(() => {})
      }
    })
  } finally {
    global.fetch = originalFetch
    Object.defineProperty(window, 'location', { value: original, configurable: true, writable: true })
  }
  return url
}

describe.each(['autoCorrect', 'customLyrics'] as const)('%s API base', (client) => {
  it.each(['localhost', '127.0.0.1', '10.0.2.2'])('uses a relative URL on local host %s', async (host) => {
    const url = await capturePostedUrl(host, client)
    expect(url).toMatch(/^\/api\/review\/job-1\//)
    expect(url).not.toContain(PROD)
  })

  it('uses the production API on the production host', async () => {
    const url = await capturePostedUrl('gen.nomadkaraoke.com', client)
    expect(url.startsWith(`${PROD}/api/review/job-1/`)).toBe(true)
  })
})
