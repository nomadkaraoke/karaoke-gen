import React from 'react'
import { act, fireEvent, render, screen } from '@testing-library/react'
import TimelineEditor from '../TimelineEditor'
import { VocalsAudioDataLoaderContext } from '../VocalsAudioDataLoader'
import { Word } from '@/lib/lyrics-review/types'

// Canvas drawing isn't available in jsdom; a plain element is enough to be the click target.
jest.mock('../WaveformVisualizer', () => ({
  WaveformVisualizer: () => <div data-testid="waveform" />,
}))

const words: Word[] = [
  { id: 'w1', text: 'wish', start_time: 11.0, end_time: 11.5, confidence: 1 },
  { id: 'w2', text: 'you', start_time: 11.6, end_time: 12.0, confidence: 1 },
]

// Segment 11–13s, padded view 10–14s, so the container's x maps 0..400px → 10..14s.
function renderEditor(props: Partial<React.ComponentProps<typeof TimelineEditor>> = {}) {
  const merged = {
    words,
    startTime: 11,
    endTime: 13,
    onWordUpdate: jest.fn(),
    onPlaySegment: jest.fn(),
    ...props,
  }
  const utils = render(
    <VocalsAudioDataLoaderContext.Provider
      value={{ audioData: { amplitudes: [0.1, 0.2], duration: 20 } as never, status: 'ready' }}
    >
      <TimelineEditor {...merged} />
    </VocalsAudioDataLoaderContext.Provider>
  )
  const container = utils.container.firstElementChild as HTMLElement
  container.getBoundingClientRect = () =>
    ({ left: 0, top: 0, width: 400, height: 100, right: 400, bottom: 100 }) as DOMRect
  return merged
}

describe('TimelineEditor waveform click-to-play', () => {
  it('plays from the clicked time in the (non-compact) Edit Segment timeline', () => {
    const props = renderEditor()
    fireEvent.click(screen.getByTestId('waveform'), { clientX: 300 })
    expect(props.onPlaySegment).toHaveBeenCalledWith(13)
  })

  it('still plays from the waveform in compact rows', () => {
    const props = renderEditor({ compact: true, showRuler: false })
    fireEvent.click(screen.getByTestId('waveform'), { clientX: 100 })
    expect(props.onPlaySegment).toHaveBeenCalledWith(11)
  })
})

describe('TimelineEditor Ctrl/Cmd-click delete', () => {
  it.each([{ ctrlKey: true }, { metaKey: true }])('deletes the word on %o click', (modifier) => {
    const onWordCtrlDelete = jest.fn()
    const props = renderEditor({ onWordCtrlDelete })
    fireEvent.mouseDown(screen.getByText('you'), modifier)
    expect(onWordCtrlDelete).toHaveBeenCalledWith(1)
    expect(props.onWordUpdate).not.toHaveBeenCalled()
  })

  it('does not delete on a plain click', () => {
    const onWordCtrlDelete = jest.fn()
    renderEditor({ onWordCtrlDelete })
    const bar = screen.getByText('you')
    fireEvent.mouseDown(bar)
    fireEvent.mouseUp(bar)
    expect(onWordCtrlDelete).not.toHaveBeenCalled()
  })

  it('deletes from the resize handles too (anywhere on the bar)', () => {
    const onWordCtrlDelete = jest.fn()
    renderEditor({ onWordCtrlDelete })
    const handle = screen.getByText('wish').previousElementSibling as HTMLElement
    fireEvent.mouseDown(handle, { ctrlKey: true })
    expect(onWordCtrlDelete).toHaveBeenCalledWith(0)
  })
})

describe('TimelineEditor resize edge auto-extend', () => {
  // Stateful host so the editor re-renders with each update, like WaveformSegmentRow.
  function Host(props: {
    initial: Word[]
    onUpdate: (words: Word[]) => void
    onCommit?: () => void
    nextBoundaryTime?: number | null
    prevBoundaryTime?: number | null
  }) {
    const [ws, setWs] = React.useState(props.initial)
    return (
      <TimelineEditor
        words={ws}
        startTime={11}
        endTime={13}
        onWordUpdate={(i, u) => {
          setWs((prev) => {
            const next = prev.map((w, j) => (j === i ? { ...w, ...u } : w))
            props.onUpdate(next)
            return next
          })
        }}
        onCommit={props.onCommit}
        nextBoundaryTime={props.nextBoundaryTime}
        prevBoundaryTime={props.prevBoundaryTime}
      />
    )
  }

  let latest: Word[]
  function setup(extra: Partial<React.ComponentProps<typeof Host>> = {}) {
    latest = words
    const onCommit = jest.fn()
    const utils = render(
      <VocalsAudioDataLoaderContext.Provider
        value={{ audioData: { amplitudes: [0.1], duration: 60 } as never, status: 'ready' }}
      >
        <Host initial={words} onUpdate={(w) => (latest = w)} onCommit={onCommit} {...extra} />
      </VocalsAudioDataLoaderContext.Provider>
    )
    const container = utils.container.firstElementChild as HTMLElement
    container.getBoundingClientRect = () =>
      ({ left: 0, top: 0, width: 400, height: 100, right: 400, bottom: 100 }) as DOMRect
    return { onCommit }
  }
  const rightHandle = (text: string) => screen.getByText(text).nextElementSibling as HTMLElement
  const leftHandle = (text: string) => screen.getByText(text).previousElementSibling as HTMLElement
  // Advance fake time in animation-frame-sized steps so the rAF loop runs.
  const hold = (ms: number) => {
    for (let t = 0; t < ms; t += 16) act(() => jest.advanceTimersByTime(16))
  }

  beforeEach(() => jest.useFakeTimers())
  afterEach(() => jest.useRealTimers())

  it('keeps extending the last word while the end handle is held at the right edge', () => {
    setup()
    // 'you' ends at 12.0s → x=200 in the 10–14s view.
    fireEvent.mouseDown(rightHandle('you'), { clientX: 200 })
    fireEvent.mouseMove(window, { clientX: 399 })
    const afterMove = latest[1].end_time!
    expect(afterMove).toBeCloseTo(13.99, 1)
    hold(1000)
    // ~4 s/s at the very edge, so a second's hold adds several seconds past the view.
    expect(latest[1].end_time!).toBeGreaterThan(afterMove + 2.5)
  })

  it('stops at the next line\'s first word', () => {
    setup({ nextBoundaryTime: 15 })
    fireEvent.mouseDown(rightHandle('you'), { clientX: 200 })
    fireEvent.mouseMove(window, { clientX: 399 })
    hold(3000)
    expect(latest[1].end_time).toBe(15)
  })

  it('stops at the next timed word in the same segment', () => {
    setup()
    fireEvent.mouseDown(rightHandle('wish'), { clientX: 150 })
    fireEvent.mouseMove(window, { clientX: 399 })
    hold(2000)
    expect(latest[0].end_time).toBe(11.6)
  })

  it('stops growing once the pointer leaves the edge zone, then tracks the pointer in the zoomed view', () => {
    setup()
    fireEvent.mouseDown(rightHandle('you'), { clientX: 200 })
    fireEvent.mouseMove(window, { clientX: 399 })
    hold(500)
    fireEvent.mouseMove(window, { clientX: 300 })
    const held = latest[1].end_time!
    // Pulled back inside the (now wider) view: shorter than at the edge, longer than the start.
    expect(held).toBeGreaterThan(12)
    hold(500)
    expect(latest[1].end_time).toBe(held)
  })

  it('does not auto-extend before the press crosses the drag threshold', () => {
    setup()
    fireEvent.mouseDown(rightHandle('you'), { clientX: 398 })
    hold(500)
    expect(latest[1].end_time).toBe(12.0)
  })

  it('extends the start handle toward the left edge, bounded by the previous line', () => {
    setup({ prevBoundaryTime: 9 })
    // 'wish' starts at 11.0s → x=100.
    fireEvent.mouseDown(leftHandle('wish'), { clientX: 100 })
    fireEvent.mouseMove(window, { clientX: 1 })
    hold(3000)
    expect(latest[0].start_time).toBe(9)
  })

  it('commits once on release, even when released outside the row', () => {
    const { onCommit } = setup()
    fireEvent.mouseDown(rightHandle('you'), { clientX: 200 })
    fireEvent.mouseMove(window, { clientX: 450 })
    hold(200)
    fireEvent.mouseUp(window)
    expect(onCommit).toHaveBeenCalledTimes(1)
    const final = latest[1].end_time
    hold(500)
    expect(latest[1].end_time).toBe(final)
  })
})
