import {
  parseRouteFromPathname,
  parseRouteFromHash,
} from '../job-router-routes'

describe('parseRouteFromPathname (local CLI mode)', () => {
  describe('non-locale paths', () => {
    it('parses /app/jobs/local/review', () => {
      expect(parseRouteFromPathname('/app/jobs/local/review')).toEqual({
        jobId: 'local',
        routeType: 'review',
      })
    })

    it('parses /app/jobs/local/instrumental', () => {
      expect(parseRouteFromPathname('/app/jobs/local/instrumental')).toEqual({
        jobId: 'local',
        routeType: 'instrumental',
      })
    })

    it('parses /app/jobs/local/audio-edit', () => {
      expect(parseRouteFromPathname('/app/jobs/local/audio-edit')).toEqual({
        jobId: 'local',
        routeType: 'audio-edit',
      })
    })

    it('tolerates a trailing slash', () => {
      expect(parseRouteFromPathname('/app/jobs/local/review/')).toEqual({
        jobId: 'local',
        routeType: 'review',
      })
    })
  })

  describe('locale-prefixed paths (LocaleRedirect output)', () => {
    /**
     * Regression: LocaleRedirect bounces /app/jobs/local/instrumental to
     * /en/app/jobs/local/instrumental. The parser must recognise the
     * locale prefix or the page falls back to the default "review"
     * routeType and the lyrics review UI is shown on the instrumental URL.
     */
    it('parses /en/app/jobs/local/instrumental', () => {
      expect(
        parseRouteFromPathname('/en/app/jobs/local/instrumental')
      ).toEqual({ jobId: 'local', routeType: 'instrumental' })
    })

    it('parses /en/app/jobs/local/review', () => {
      expect(parseRouteFromPathname('/en/app/jobs/local/review')).toEqual({
        jobId: 'local',
        routeType: 'review',
      })
    })

    it('parses other 2-letter locale prefixes', () => {
      for (const locale of ['es', 'de', 'fr', 'ja', 'pt']) {
        expect(
          parseRouteFromPathname(`/${locale}/app/jobs/local/instrumental`)
        ).toEqual({ jobId: 'local', routeType: 'instrumental' })
      }
    })
  })

  describe('miss cases', () => {
    it('returns unknown for empty pathname', () => {
      expect(parseRouteFromPathname('')).toEqual({
        jobId: null,
        routeType: 'unknown',
      })
    })

    it('returns unknown for unrelated paths', () => {
      expect(parseRouteFromPathname('/app/admin')).toEqual({
        jobId: null,
        routeType: 'unknown',
      })
    })

    it('returns unknown for an unknown action verb', () => {
      expect(parseRouteFromPathname('/app/jobs/local/foobar')).toEqual({
        jobId: null,
        routeType: 'unknown',
      })
    })

    it('returns unknown for paths missing an action', () => {
      expect(parseRouteFromPathname('/app/jobs/local')).toEqual({
        jobId: null,
        routeType: 'unknown',
      })
    })
  })
})

describe('parseRouteFromHash (cloud mode)', () => {
  it('parses #/{jobId}/review', () => {
    expect(parseRouteFromHash('#/abc123/review')).toEqual({
      jobId: 'abc123',
      routeType: 'review',
    })
  })

  it('parses #abc123/instrumental (no leading slash)', () => {
    expect(parseRouteFromHash('#abc123/instrumental')).toEqual({
      jobId: 'abc123',
      routeType: 'instrumental',
    })
  })

  it('returns unknown for empty or "#"-only hash', () => {
    expect(parseRouteFromHash('')).toEqual({ jobId: null, routeType: 'unknown' })
    expect(parseRouteFromHash('#')).toEqual({
      jobId: null,
      routeType: 'unknown',
    })
  })
})
