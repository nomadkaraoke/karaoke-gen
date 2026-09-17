/**
 * @jest-environment jsdom
 *
 * The review hot path no longer signs GCS URLs (2026-09-17 fast-load re-architecture):
 * the backend returns each instrumental option's `audio_url` as a RELATIVE same-origin
 * proxy path, and the API client rewrites it into an absolute, token-authenticated URL
 * usable as a raw <audio> src (a media src can't send an Authorization header). These
 * tests lock in that rewrite for getCorrectionData and refreshInstrumentalUrls.
 */

import { lyricsReviewApi, createLyricsReviewApiClient } from '@/lib/api'

const fetchMock = jest.fn()

function jsonResponse(body: unknown) {
  return { ok: true, status: 200, json: async () => body }
}

describe('instrumental audio proxy URL resolution', () => {
  beforeEach(() => {
    fetchMock.mockReset()
    ;(global as any).fetch = fetchMock
    window.localStorage.setItem('karaoke_access_token', 'tok-123')
  })

  afterEach(() => {
    window.localStorage.removeItem('karaoke_access_token')
  })

  it('rewrites relative proxy paths into absolute token URLs on getCorrectionData', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        corrected_segments: [],
        instrumental_options: [
          { id: 'clean', label: 'Clean', audio_url: '/api/review/job1/instrumental-audio/clean' },
          { id: 'with_backing', label: 'Backing', audio_url: '/api/review/job1/instrumental-audio/with_backing' },
        ],
      }),
    )

    const data = await lyricsReviewApi.getCorrectionData('job1')
    const urls = Object.fromEntries(
      (data.instrumental_options ?? []).map((o) => [o.id, o.audio_url]),
    )
    // API_BASE_URL is '' (same-origin) under jsdom → relative path + token.
    expect(urls.clean).toBe('/api/review/job1/instrumental-audio/clean?token=tok-123')
    expect(urls.with_backing).toBe('/api/review/job1/instrumental-audio/with_backing?token=tok-123')
  })

  it('leaves already-absolute (dev proxy) URLs and null audio_url untouched', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        corrected_segments: [],
        instrumental_options: [
          { id: 'clean', label: 'Clean', audio_url: 'http://127.0.0.1:8000/api/review/job1/dev-audio?path=x' },
          { id: 'with_backing', label: 'Backing', audio_url: null },
        ],
      }),
    )

    const data = await lyricsReviewApi.getCorrectionData('job1')
    const urls = Object.fromEntries(
      (data.instrumental_options ?? []).map((o) => [o.id, o.audio_url]),
    )
    expect(urls.clean).toBe('http://127.0.0.1:8000/api/review/job1/dev-audio?path=x')
    expect(urls.with_backing).toBeNull()
  })

  it('resolves proxy paths on refreshInstrumentalUrls too', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        instrumental_options: [
          { id: 'clean', label: 'Clean', audio_url: '/api/review/job1/instrumental-audio/clean' },
        ],
      }),
    )

    const options = await createLyricsReviewApiClient('job1').refreshInstrumentalUrls()
    expect(options[0].audio_url).toBe('/api/review/job1/instrumental-audio/clean?token=tok-123')
  })
})
