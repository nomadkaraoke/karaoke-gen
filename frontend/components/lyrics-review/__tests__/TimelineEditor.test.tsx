import { fireEvent, render, screen } from '@testing-library/react'
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
      value={{ audioData: { amplitudes: [0.1, 0.2], duration: 20 } as never }}
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
