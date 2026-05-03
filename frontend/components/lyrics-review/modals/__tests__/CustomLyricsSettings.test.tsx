import { render, screen, fireEvent } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import messages from '@/messages/en.json'
import CustomLyricsSettings from '../CustomLyricsSettings'
import { DEFAULT_GENERATION_SETTINGS, GenerationSettings } from '@/lib/api/customLyrics'

function renderWith(settings: GenerationSettings, onChange = jest.fn()) {
  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <CustomLyricsSettings settings={settings} onChange={onChange} />
    </NextIntlClientProvider>,
  )
}

describe('CustomLyricsSettings', () => {
  it('renders all 3 toggles and slider', () => {
    renderWith(DEFAULT_GENERATION_SETTINGS)
    expect(screen.getByText(/Allow rewording client lyrics/i)).toBeInTheDocument()
    expect(screen.getByText(/Allow omitting client lyrics/i)).toBeInTheDocument()
    expect(screen.getByText(/Maintain original segment count/i)).toBeInTheDocument()
    expect(screen.getByText(/Singability strictness/i)).toBeInTheDocument()
  })

  it('toggling a switch fires onChange with updated settings', () => {
    const onChange = jest.fn()
    renderWith(DEFAULT_GENERATION_SETTINGS, onChange)
    const rewordSwitch = screen.getByRole('switch', { name: /Allow rewording/i })
    fireEvent.click(rewordSwitch)
    expect(onChange).toHaveBeenCalledWith({
      ...DEFAULT_GENERATION_SETTINGS,
      allow_reword: false,
    })
  })

  it('shows contradiction warning when reword=OFF and strictness=Strict', () => {
    renderWith({ ...DEFAULT_GENERATION_SETTINGS, allow_reword: false, strictness: 'strict' })
    expect(
      screen.getByText(/AI may be unable to match syllable counts/i),
    ).toBeInTheDocument()
  })

  it('shows contradiction warning when reword=OFF and strictness=Tight', () => {
    renderWith({ ...DEFAULT_GENERATION_SETTINGS, allow_reword: false, strictness: 'tight' })
    expect(
      screen.getByText(/AI may be unable to match syllable counts/i),
    ).toBeInTheDocument()
  })

  it('does not show warning at safe combinations', () => {
    renderWith(DEFAULT_GENERATION_SETTINGS)
    expect(
      screen.queryByText(/AI may be unable to match syllable counts/i),
    ).not.toBeInTheDocument()
  })

  it('clicking a strictness label updates the slider', () => {
    const onChange = jest.fn()
    renderWith(DEFAULT_GENERATION_SETTINGS, onChange)
    fireEvent.click(screen.getByText('Tight'))
    expect(onChange).toHaveBeenCalledWith({
      ...DEFAULT_GENERATION_SETTINGS,
      strictness: 'tight',
    })
  })
})
