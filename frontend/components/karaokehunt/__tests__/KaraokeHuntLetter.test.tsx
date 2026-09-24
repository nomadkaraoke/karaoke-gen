import { render, screen } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import messages from '@/messages/en.json'
import KaraokeHuntLetter from '../KaraokeHuntLetter'

jest.mock('@/components/LanguageSwitcher', () => () => null)
jest.mock('@/components/ThemeToggle', () => ({ ThemeToggle: () => null }))

function renderLetter(namespace: 'karaokehuntLetter' | 'karaokehuntListLetter', paragraphs: string[]) {
  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <KaraokeHuntLetter namespace={namespace} paragraphs={paragraphs} />
    </NextIntlClientProvider>,
  )
}

describe('KaraokeHuntLetter', () => {
  it('renders the requester letter (/karaokehunt) with its apology and retired-app note', () => {
    renderLetter('karaokehuntLetter', ['apology', 'goodNews', 'credits', 'appRetired', 'thanks'])
    expect(screen.getByRole('heading', { name: messages.karaokehuntLetter.title })).toBeInTheDocument()
    expect(screen.getByText(messages.karaokehuntLetter.apology)).toBeInTheDocument()
    expect(screen.getByText(messages.karaokehuntLetter.appRetired)).toBeInTheDocument()
  })

  it('renders the app-user letter (/karaokehuntlist) without the song-request apology', () => {
    renderLetter('karaokehuntListLetter', ['apology', 'goodNews', 'credits', 'thanks'])
    expect(screen.getByText(messages.karaokehuntListLetter.apology)).toBeInTheDocument()
    expect(screen.getByText(messages.karaokehuntListLetter.thanks)).toBeInTheDocument()
    expect(screen.queryByText(/requested a song/i)).not.toBeInTheDocument()
  })

  it('renders the credits paragraph with a sign-in link to gen', () => {
    renderLetter('karaokehuntListLetter', ['credits'])
    const link = screen.getByRole('link', { name: 'gen.nomadkaraoke.com' })
    expect(link).toHaveAttribute('href', 'https://gen.nomadkaraoke.com')
    expect(screen.getByText(/3 free credits/)).toBeInTheDocument()
  })
})
