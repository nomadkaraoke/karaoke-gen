import { act, renderHook } from '@testing-library/react'
import {
  LATE_STEMS_MAX_POLLS,
  LATE_STEMS_POLL_MS,
  useLateInstrumentalStems,
} from '../useLateInstrumentalStems'

const notYet = { instrumental_options: [], audio_complete: false, job_status: 'in_review' }
const done = {
  instrumental_options: [
    { id: 'clean', label: 'Clean', audio_url: 'http://x/clean' },
    { id: 'with_backing', label: 'Backing', audio_url: 'http://x/backing' },
  ],
  audio_complete: true,
  job_status: 'in_review',
  backing_vocals_analysis: { recommended_selection: 'with_backing' },
  auto_approval: { backing: { confident: true, resolved_selection: 'with_backing' } },
}

// Let the awaited poll promise settle.
const flush = () => act(async () => {})

describe('useLateInstrumentalStems', () => {
  beforeEach(() => jest.useFakeTimers())
  afterEach(() => jest.useRealTimers())

  it('stays idle and never polls when disabled (stems were in the page-load data)', async () => {
    const get = jest.fn()
    const { result } = renderHook(() => useLateInstrumentalStems({ enabled: false, getInstrumentalState: get }))
    await flush()
    expect(result.current.status).toBe('idle')
    expect(get).not.toHaveBeenCalled()
  })

  it('polls while separation runs, then returns the fresh state once audio_complete', async () => {
    const get = jest.fn().mockResolvedValueOnce(notYet).mockResolvedValueOnce(notYet).mockResolvedValue(done)
    const { result } = renderHook(() => useLateInstrumentalStems({ enabled: true, getInstrumentalState: get }))
    await flush()
    expect(result.current.status).toBe('waiting')
    expect(get).toHaveBeenCalledTimes(1)

    await act(async () => { jest.advanceTimersByTime(LATE_STEMS_POLL_MS) })
    expect(get).toHaveBeenCalledTimes(2)
    expect(result.current.status).toBe('waiting')

    await act(async () => { jest.advanceTimersByTime(LATE_STEMS_POLL_MS) })
    expect(result.current.status).toBe('ready')
    expect(result.current.state).toEqual(done)

    // Stops polling once ready.
    await act(async () => { jest.advanceTimersByTime(LATE_STEMS_POLL_MS * 3) })
    expect(get).toHaveBeenCalledTimes(3)
  })

  it('does not restart polling when the getter identity changes each render', async () => {
    const get = jest.fn().mockResolvedValue(notYet)
    const { rerender } = renderHook(
      ({ g }) => useLateInstrumentalStems({ enabled: true, getInstrumentalState: g }),
      { initialProps: { g: get } }
    )
    await flush()
    rerender({ g: (() => get()) as typeof get })
    rerender({ g: (() => get()) as typeof get })
    await flush()
    expect(get).toHaveBeenCalledTimes(1)
  })

  it('keeps polling through transient errors', async () => {
    const warn = jest.spyOn(console, 'warn').mockImplementation(() => {})
    const get = jest.fn().mockRejectedValueOnce(new Error('503')).mockResolvedValue(done)
    const { result } = renderHook(() => useLateInstrumentalStems({ enabled: true, getInstrumentalState: get }))
    await flush()
    expect(result.current.status).toBe('waiting')
    await act(async () => { jest.advanceTimersByTime(LATE_STEMS_POLL_MS) })
    expect(result.current.status).toBe('ready')
    warn.mockRestore()
  })

  it('gives up when the job failed', async () => {
    const get = jest.fn().mockResolvedValue({ ...notYet, job_status: 'failed' })
    const { result } = renderHook(() => useLateInstrumentalStems({ enabled: true, getInstrumentalState: get }))
    await flush()
    expect(result.current.status).toBe('gave_up')
  })

  it('gives up after the polling cap', async () => {
    const get = jest.fn().mockResolvedValue(notYet)
    const { result } = renderHook(() => useLateInstrumentalStems({ enabled: true, getInstrumentalState: get }))
    await flush()
    for (let i = 1; i < LATE_STEMS_MAX_POLLS; i++) {
      await act(async () => { jest.advanceTimersByTime(LATE_STEMS_POLL_MS) })
    }
    expect(get).toHaveBeenCalledTimes(LATE_STEMS_MAX_POLLS)
    expect(result.current.status).toBe('gave_up')
  })
})
