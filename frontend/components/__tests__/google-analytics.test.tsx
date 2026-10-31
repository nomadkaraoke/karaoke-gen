import { render } from '@testing-library/react'
import { GoogleAnalytics } from '../google-analytics'

// next/script injects tags itself; render a marker so the test can see what would load.
jest.mock('next/script', () => ({
  __esModule: true,
  default: ({ id, src }: { id?: string; src?: string }) => (
    <span data-testid="next-script" data-id={id} data-src={src} />
  ),
}))

function renderOn(hostname: string) {
  const original = window.location
  delete (window as unknown as { location?: Location }).location
  Object.defineProperty(window, 'location', { value: { ...original, hostname } as Location, configurable: true, writable: true })
  try {
    return render(<GoogleAnalytics />)
  } finally {
    Object.defineProperty(window, 'location', { value: original, configurable: true, writable: true })
  }
}

describe('GoogleAnalytics', () => {
  it.each(['localhost', '127.0.0.1', '10.0.2.2'])(
    'loads nothing on local host %s (local stack / E2E must not send hits to the real property)',
    (host) => {
      const { queryAllByTestId } = renderOn(host)
      expect(queryAllByTestId('next-script')).toHaveLength(0)
    }
  )

  it('loads gtag on the production host', () => {
    const { getAllByTestId } = renderOn('gen.nomadkaraoke.com')
    const scripts = getAllByTestId('next-script')
    expect(scripts.some((s) => (s.getAttribute('data-src') ?? '').includes('googletagmanager.com/gtag/js'))).toBe(true)
  })
})
