import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

const setSyncReference = jest.fn().mockResolvedValue(undefined)
jest.mock('@/lib/api', () => ({
  ...jest.requireActual('@/lib/api'),
  createLyricsReviewApiClient: () => ({ setSyncReference }),
}))

// eslint-disable-next-line import/first
import { ReplaySyncVerdict } from '@/app/[locale]/app/jobs/[[...slug]]/client'

describe('ReplaySyncVerdict (replay triage for the timing reference set)', () => {
  beforeEach(() => {
    setSyncReference.mockClear()
    window.history.replaceState({}, '', '/en/app/jobs?replay=1&queue=a1,b2,c3#/b2/review')
  })

  it('saves "perfectly synced" and moves to the next queued job', async () => {
    render(<ReplaySyncVerdict jobId="b2" saved={null} />)
    expect(screen.getByTestId('replay-sync-status')).toHaveTextContent('Not triaged yet')
    await userEvent.click(screen.getByRole('button', { name: 'Perfectly synced' }))
    await userEvent.click(screen.getByRole('button', { name: /save & next job/i }))
    expect(setSyncReference).toHaveBeenCalledWith(true, undefined, undefined)
    expect(window.location.hash).toBe('#/c3/review')
  })

  it('saves "not perfect" with reasons and notes', async () => {
    render(<ReplaySyncVerdict jobId="b2" saved={null} />)
    await userEvent.click(screen.getByRole('button', { name: 'Not perfect' }))
    await userEvent.click(screen.getByLabelText('timing off in places'))
    await userEvent.type(screen.getByPlaceholderText('Notes (optional)'), 'chorus 2 late')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(setSyncReference).toHaveBeenCalledWith(false, 'chorus 2 late', ['timing off in places'])
    expect(window.location.hash).toBe('#/b2/review')
  })

  it('shows the saved verdict and keeps Save disabled until a choice exists', () => {
    render(<ReplaySyncVerdict jobId="b2" saved={{ verified: true, at: '2026-11-04T10:00:00+00:00' }} />)
    expect(screen.getByTestId('replay-sync-status')).toHaveTextContent('Saved: perfectly synced')
  })

  it('flags an unsaved change until it is saved', async () => {
    render(<ReplaySyncVerdict jobId="b2" saved={{ verified: true }} />)
    await userEvent.click(screen.getByRole('button', { name: 'Not perfect' }))
    expect(screen.getByTestId('replay-sync-status')).toHaveTextContent('Unsaved changes')
    await userEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(screen.getByTestId('replay-sync-status')).toHaveTextContent('Saved: not perfect')
  })

  it('keeps Save disabled until a choice exists', () => {
    render(<ReplaySyncVerdict jobId="a1" saved={null} />)
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  })
})
