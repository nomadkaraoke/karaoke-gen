/**
 * @jest-environment jsdom
 */
import { installGlobalErrorHandlers, isOpaqueCrossOriginError } from '@/lib/client-error-setup'
import { reportClientError } from '@/lib/crash-reporter'

jest.mock('@/lib/crash-reporter', () => ({ reportClientError: jest.fn() }))
jest.mock('@/lib/version-check', () => ({
  hardReload: jest.fn(() => false),
  isChunkLoadError: jest.fn(() => false),
  isStale: jest.fn(() => Promise.resolve(null)),
  startAmbientVersionPoll: jest.fn(),
}))

const mockReport = reportClientError as jest.MockedFunction<typeof reportClientError>

const flush = () => new Promise((r) => setTimeout(r, 0))

describe('isOpaqueCrossOriginError', () => {
  it('matches the bare cross-origin "Script error." with no error object', () => {
    expect(isOpaqueCrossOriginError({ error: null, message: 'Script error.' })).toBe(true)
    expect(isOpaqueCrossOriginError({ error: undefined, message: 'Script error' })).toBe(true)
    expect(isOpaqueCrossOriginError({ error: null, message: ' script error. ' })).toBe(true)
  })

  it('does not match when a real error object is present', () => {
    expect(isOpaqueCrossOriginError({ error: new Error('Script error.'), message: 'Script error.' })).toBe(false)
  })

  it('does not match other messages', () => {
    expect(isOpaqueCrossOriginError({ error: null, message: 'Uncaught TypeError: x is undefined' })).toBe(false)
    expect(isOpaqueCrossOriginError({ error: null, message: 'Script error in foo' })).toBe(false)
  })
})

describe('installGlobalErrorHandlers window error listener', () => {
  beforeAll(() => installGlobalErrorHandlers(() => null))
  beforeEach(() => mockReport.mockClear())

  it('skips opaque cross-origin script errors', async () => {
    window.dispatchEvent(new ErrorEvent('error', { message: 'Script error.' }))
    await flush()
    expect(mockReport).not.toHaveBeenCalled()
  })

  it('still reports errors that carry a real error object', async () => {
    const err = new TypeError('Cannot read properties of undefined')
    window.dispatchEvent(new ErrorEvent('error', { message: err.message, error: err }))
    await flush()
    expect(mockReport).toHaveBeenCalledTimes(1)
    expect(mockReport.mock.calls[0][0]).toMatchObject({ error: err, source: 'window.onerror' })
  })
})
