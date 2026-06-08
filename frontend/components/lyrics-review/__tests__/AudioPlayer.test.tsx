/**
 * @jest-environment jsdom
 */
import { render, screen, act } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import enMessages from '@/messages/en.json'
import AudioPlayer, { AUDIO_READY_EVENT } from '../AudioPlayer'

// Capture every Audio element AudioPlayer constructs so tests can drive media events.
let created: HTMLAudioElement[]

beforeEach(() => {
  created = []
  ;(global as unknown as { Audio: unknown }).Audio = jest.fn().mockImplementation((src?: string) => {
    const a = document.createElement('audio')
    if (src) a.src = src
    // jsdom doesn't implement play(); make it a resolved promise so .catch() is safe.
    a.play = jest.fn().mockResolvedValue(undefined)
    a.pause = jest.fn()
    created.push(a)
    return a
  })
})

afterEach(() => {
  delete (window as Window).isAudioReady
  delete (window as Window).seekAndPlayAudio
  delete (window as Window).toggleAudioPlayback
  jest.restoreAllMocks()
})

function renderPlayer(audioUrl: string | null = 'https://example.test/audio.mp3') {
  return render(
    <NextIntlClientProvider locale="en" messages={enMessages}>
      <AudioPlayer audioUrl={audioUrl} />
    </NextIntlClientProvider>
  )
}

function fireOnAudio(type: string) {
  act(() => {
    created[0].dispatchEvent(new Event(type))
  })
}

describe('AudioPlayer readiness gating', () => {
  it('renders nothing without an audioUrl', () => {
    const { container } = renderPlayer(null)
    expect(container).toBeEmptyDOMElement()
  })

  it('shows the loading state and is not ready before canplaythrough', () => {
    renderPlayer()
    expect(screen.getByText('Loading audio…')).toBeInTheDocument()
    // No play button while loading.
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(window.isAudioReady).toBe(false)
  })

  it('imperative globals no-op until ready, then play', () => {
    renderPlayer()
    // Before ready: seekAndPlay must not touch the media element.
    act(() => {
      window.seekAndPlayAudio?.(12)
    })
    expect(created[0].play).not.toHaveBeenCalled()

    fireOnAudio('canplaythrough')
    expect(window.isAudioReady).toBe(true)

    act(() => {
      window.seekAndPlayAudio?.(12)
    })
    expect(created[0].play).toHaveBeenCalledTimes(1)
    expect(created[0].currentTime).toBe(12)
  })

  it('reveals playback controls and publishes ready once canplaythrough fires', () => {
    const onReady = jest.fn()
    window.addEventListener(AUDIO_READY_EVENT, onReady)
    renderPlayer()

    fireOnAudio('canplaythrough')

    expect(screen.queryByText('Loading audio…')).not.toBeInTheDocument()
    expect(screen.getByRole('button')).toBeInTheDocument()
    expect(onReady).toHaveBeenCalled()
    window.removeEventListener(AUDIO_READY_EVENT, onReady)
  })

  it('shows an error state and stays not-ready on media error', () => {
    renderPlayer()
    fireOnAudio('error')
    expect(screen.getByText("Couldn't load audio")).toBeInTheDocument()
    expect(window.isAudioReady).toBe(false)
  })
})
