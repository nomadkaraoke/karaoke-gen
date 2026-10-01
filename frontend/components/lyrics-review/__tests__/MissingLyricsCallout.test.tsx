import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import MissingLyricsCallout, { MissingLyricsResyncHint } from '../MissingLyricsCallout'
import { findOpenMissingLyricsGaps } from '@/lib/lyrics-review/utils/missingLyrics'
import type { LyricsSegment, VocalGap } from '@/lib/lyrics-review/types'

const gap = (overrides: Partial<VocalGap> = {}): VocalGap => ({
  start: 20.66,
  end: 32.4,
  duration: 11.74,
  active_fraction: 0.9,
  longest_run_s: 11.76,
  reference_lines: { genius: ['plain line'] },
  synced_reference_lines: { lrclib: ['synced line one', 'synced line two'] },
  suspect: true,
  evidenced: true,
  ...overrides,
})

const segments: LyricsSegment[] = [
  { id: 's1', text: 'a', start_time: 10, end_time: 20.66, words: [{ id: 'w1', text: 'a', start_time: 10, end_time: 20.66 }] },
]

const openGaps = (gaps: VocalGap[] | null, segs = segments) =>
  findOpenMissingLyricsGaps(gaps === null ? null : { gaps }, segs)

describe('MissingLyricsCallout', () => {
  it('shows the warning, time range and synced reference lines for an evidenced gap', () => {
    render(<MissingLyricsCallout gaps={openGaps([gap()])} isReadOnly={false} onInsert={jest.fn()} />)
    expect(screen.getByText('Possible missing lyrics at 0:20–0:32')).toBeInTheDocument()
    expect(
      screen.getByText('The lead vocal is singing here but there are no lyrics.')
    ).toBeInTheDocument()
    expect(screen.getByText('Lyrics expected here (from lrclib):')).toBeInTheDocument()
    expect(screen.getByText('synced line one')).toBeInTheDocument()
    expect(screen.getByText('synced line two')).toBeInTheDocument()
    expect(screen.queryByText('plain line')).not.toBeInTheDocument()
  })

  it('falls back to reference_lines when there are no synced lines', () => {
    render(
      <MissingLyricsCallout
        gaps={openGaps([gap({ synced_reference_lines: {} })])}
        isReadOnly={false}
        onInsert={jest.fn()}
      />
    )
    expect(screen.getByText('Lyrics expected here (from genius):')).toBeInTheDocument()
    expect(screen.getByText('plain line')).toBeInTheDocument()
  })

  it('renders nothing when words already exist in the gap', () => {
    const filled: LyricsSegment[] = [
      ...segments,
      { id: 's2', text: 'b', start_time: 22, end_time: 23, words: [{ id: 'w2', text: 'b', start_time: 22, end_time: 23 }] },
    ]
    const { container } = render(
      <MissingLyricsCallout gaps={openGaps([gap()], filled)} isReadOnly={false} onInsert={jest.fn()} />
    )
    expect(container.firstChild).toBeNull()
  })

  it('renders nothing for non-evidenced gaps or null data', () => {
    const { container, rerender } = render(
      <MissingLyricsCallout gaps={openGaps([gap({ evidenced: false })])} isReadOnly={false} onInsert={jest.fn()} />
    )
    expect(container.firstChild).toBeNull()
    rerender(<MissingLyricsCallout gaps={openGaps(null)} isReadOnly={false} onInsert={jest.fn()} />)
    expect(container.firstChild).toBeNull()
  })

  it('calls onInsert with the gap when "Insert these lines" is clicked', async () => {
    const onInsert = jest.fn()
    const gaps = openGaps([gap()])
    render(<MissingLyricsCallout gaps={gaps} isReadOnly={false} onInsert={onInsert} />)
    await userEvent.setup().click(screen.getByRole('button', { name: /Insert these lines/ }))
    expect(onInsert).toHaveBeenCalledWith(gaps[0])
  })

  it('plays from the gap start', async () => {
    const onPlay = jest.fn()
    render(<MissingLyricsCallout gaps={openGaps([gap()])} isReadOnly={false} onInsert={jest.fn()} onPlay={onPlay} />)
    await userEvent.setup().click(screen.getByRole('button', { name: /Play/ }))
    expect(onPlay).toHaveBeenCalledWith(20.66)
  })

  it('shows the marker but disables insertion in read-only mode', () => {
    const onInsert = jest.fn()
    render(<MissingLyricsCallout gaps={openGaps([gap()])} isReadOnly onInsert={onInsert} />)
    expect(screen.getByText('Possible missing lyrics at 0:20–0:32')).toBeInTheDocument()
    const btn = screen.getByRole('button', { name: /Insert these lines/ })
    expect(btn).toBeDisabled()
    // The disabled button can't receive hover (pointer-events-none), so the explanation
    // sits on a focusable wrapper.
    const wrapper = screen.getByTestId('missing-lyrics-insert-wrapper')
    expect(wrapper).toHaveAttribute('title', 'Editing is disabled in read-only mode')
    expect(wrapper).toHaveAttribute('tabindex', '0')
    expect(btn).not.toHaveAttribute('title')
    expect(btn).toHaveAccessibleName(/read-only/)
  })

  it('applies the timing offset to the shown range and the Play time', async () => {
    const onPlay = jest.fn()
    render(
      <MissingLyricsCallout
        gaps={openGaps([gap()])}
        isReadOnly={false}
        onInsert={jest.fn()}
        onPlay={onPlay}
        timingOffsetMs={2000}
      />
    )
    expect(screen.getByText('Possible missing lyrics at 0:22–0:34')).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole('button', { name: /Play/ }))
    expect(onPlay).toHaveBeenCalledWith(22.66)
  })

  it('pending gap (untimed lyrics already there): no Insert, shows a Sync timing prompt', async () => {
    const onSync = jest.fn()
    const pendingGap = { ...openGaps([gap()])[0], pendingSegmentIndex: 3 }
    render(<MissingLyricsCallout gaps={[pendingGap]} isReadOnly={false} onInsert={jest.fn()} onSync={onSync} />)
    expect(screen.queryByRole('button', { name: /Insert these lines/ })).not.toBeInTheDocument()
    expect(screen.getByTestId('missing-lyrics-pending')).toHaveTextContent(/aren't timed yet/)
    await userEvent.setup().click(screen.getByTestId('missing-lyrics-pending-sync'))
    expect(onSync).toHaveBeenCalledWith(3)
  })

  it('hides the insert button and explains when no reference lines are available', () => {
    render(
      <MissingLyricsCallout
        gaps={openGaps([gap({ synced_reference_lines: {}, reference_lines: {} })])}
        isReadOnly={false}
        onInsert={jest.fn()}
      />
    )
    expect(screen.queryByRole('button', { name: /Insert these lines/ })).not.toBeInTheDocument()
    expect(screen.getByText(/No reference lines are available/)).toBeInTheDocument()
  })
})

describe('MissingLyricsResyncHint', () => {
  const lines = [
    { segment: { id: 'a', text: 'first inserted', words: [], start_time: 1, end_time: 2 }, index: 3 },
    { segment: { id: 'b', text: 'second inserted', words: [], start_time: 2, end_time: 3 }, index: 4 },
  ]

  it('lists inserted lines with a Sync timing button each, opening the right segment', async () => {
    const onSync = jest.fn()
    render(<MissingLyricsResyncHint lines={lines} onSync={onSync} onDismiss={jest.fn()} />)
    expect(screen.getByText('Check the timing of the inserted lines')).toBeInTheDocument()
    expect(screen.getByText(/use Tap To Sync/)).toBeInTheDocument()
    const buttons = screen.getAllByRole('button', { name: /Sync timing/ })
    expect(buttons).toHaveLength(2)
    await userEvent.setup().click(buttons[1])
    expect(onSync).toHaveBeenCalledWith(4)
  })

  it('can be dismissed and renders nothing with no lines', async () => {
    const onDismiss = jest.fn()
    const { container, rerender } = render(
      <MissingLyricsResyncHint lines={lines} onSync={jest.fn()} onDismiss={onDismiss} />
    )
    await userEvent.setup().click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(onDismiss).toHaveBeenCalled()
    rerender(<MissingLyricsResyncHint lines={[]} onSync={jest.fn()} onDismiss={onDismiss} />)
    expect(container.firstChild).toBeNull()
  })
})
