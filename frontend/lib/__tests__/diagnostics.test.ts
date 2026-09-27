/**
 * @jest-environment jsdom
 */
import {
  __resetDiagnosticsForTest,
  addBreadcrumb,
  collectDiagnostics,
  getBreadcrumbs,
  installDiagnostics,
  safeHash,
} from '@/lib/diagnostics'

describe('diagnostics', () => {
  beforeAll(() => {
    global.URL.createObjectURL = jest.fn(() => 'blob:x')
    global.URL.revokeObjectURL = jest.fn()
    installDiagnostics()
  })
  beforeEach(() => __resetDiagnosticsForTest())

  it('keeps only the most recent breadcrumbs, truncated', () => {
    for (let i = 0; i < 40; i++) addBreadcrumb('test', `crumb ${i} ${'x'.repeat(200)}`)
    const crumbs = getBreadcrumbs()
    expect(crumbs).toHaveLength(30)
    expect(crumbs[0].message.startsWith('crumb 10 ')).toBe(true)
    expect(crumbs[29].message.length).toBeLessThanOrEqual(120)
  })

  it('records clicks on buttons with their label', () => {
    document.body.innerHTML = '<button aria-label="Create Karaoke Video"><span id="inner">Go</span></button>'
    document.getElementById('inner')!.click()
    expect(getBreadcrumbs().at(-1)).toMatchObject({ category: 'click', message: 'button "Create Karaoke Video"' })
  })

  it('records client-side navigation', () => {
    window.history.pushState({}, '', '/en/app/jobs')
    expect(getBreadcrumbs().at(-1)).toMatchObject({ category: 'nav', message: 'pushState /en/app/jobs' })
  })

  it('never records tokens from the URL hash', () => {
    expect(safeHash('#/3ea34552/review?token=abc123')).toBe('#/3ea34552/review')
    expect(safeHash('#/j/instrumental&t=secret')).toBe('#/j/instrumental')
    window.history.pushState({}, '', '/en/app/jobs#/j1/review?token=s3cret')
    expect(getBreadcrumbs().at(-1)!.message).toBe('pushState /en/app/jobs#/j1/review')
    expect(JSON.stringify(getBreadcrumbs())).not.toContain('s3cret')
  })

  it('tracks outstanding blob URLs', () => {
    const a = URL.createObjectURL(new Blob(['a']))
    URL.createObjectURL(new Blob(['b']))
    URL.revokeObjectURL(a)
    expect(collectDiagnostics().live_blob_urls).toBe(1)
  })

  it('snapshots page resources', () => {
    document.body.innerHTML = '<canvas></canvas><audio></audio><audio></audio>'
    const snap = collectDiagnostics()
    expect(snap).toMatchObject({ canvas_elements: 1, audio_elements: 2, video_elements: 0 })
    expect(snap.dom_nodes).toBeGreaterThan(3)
    expect(typeof snap.page_age_s).toBe('number')
  })
})
