import { attributionFromLanding, captureAttribution, getAttribution } from '../attribution'

describe('attributionFromLanding', () => {
  it('captures utm params and the landing path', () => {
    const rec = attributionFromLanding('?utm_source=youtube&utm_medium=description&utm_campaign=make_cta&code=youtube', '', '/en/r')
    expect(rec).toMatchObject({
      utm_source: 'youtube',
      utm_medium: 'description',
      utm_campaign: 'make_cta',
      landing_path: '/en/r',
    })
    expect(rec?.captured_at).toBeTruthy()
  })

  it('captures an external referrer host without utm', () => {
    const rec = attributionFromLanding('', 'https://www.youtube.com/watch?v=abc', '/en')
    expect(rec?.referrer).toBe('www.youtube.com')
    expect(rec?.utm_source).toBeUndefined()
  })

  it('counts sister sites as referrers', () => {
    expect(attributionFromLanding('', 'https://nomadkaraoke.com/en/', '/en')?.referrer).toBe('nomadkaraoke.com')
  })

  it('ignores gen itself and plain direct visits', () => {
    expect(attributionFromLanding('', 'https://gen.nomadkaraoke.com/en/app', '/en')).toBeNull()
    expect(attributionFromLanding('', `${window.location.origin}/en/app`, '/en')).toBeNull()
    expect(attributionFromLanding('?ref=friend', '', '/en')).toBeNull()
    expect(attributionFromLanding('', 'not a url', '/en')).toBeNull()
  })
})

describe('captureAttribution', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('keeps the first touch and never overwrites it', () => {
    window.history.replaceState({}, '', '/en?utm_source=youtube')
    captureAttribution()
    window.history.replaceState({}, '', '/en?utm_source=reddit')
    captureAttribution()
    expect(getAttribution()?.utm_source).toBe('youtube')
  })

  it('stores nothing for a direct visit', () => {
    window.history.replaceState({}, '', '/en/app')
    captureAttribution()
    expect(getAttribution()).toBeNull()
  })

  it('drops a record with an unparseable captured_at', () => {
    localStorage.setItem('nk_attribution', JSON.stringify({ utm_source: 'youtube', captured_at: 'garbage' }))
    expect(getAttribution()).toBeNull()
  })

  it('expires attribution older than 30 days', () => {
    const old = new Date(Date.now() - 31 * 24 * 60 * 60 * 1000).toISOString()
    localStorage.setItem('nk_attribution', JSON.stringify({ utm_source: 'youtube', captured_at: old }))
    expect(getAttribution()).toBeNull()
    expect(localStorage.getItem('nk_attribution')).toBeNull()
  })
})
