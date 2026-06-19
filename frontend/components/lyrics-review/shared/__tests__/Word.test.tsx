import { render, screen } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import enMessages from '@/messages/en.json'
import { WordComponent } from '../Word'
import { HIGHLIGHT_CLASSES } from '@/lib/lyrics-review/constants'

const ORANGE = HIGHLIGHT_CLASSES.uncorrectedGap // 'bg-orange-500/25'

function renderWord(props: any) {
  return render(
    <NextIntlClientProvider locale="en" messages={enMessages}>
      <WordComponent word="hello" shouldFlash={false} {...props} />
    </NextIntlClientProvider>,
  )
}

describe('WordComponent gap-orange fallback (no reference lyrics)', () => {
  it('renders a plain word with NO background colour by default', () => {
    renderWord({})
    expect(screen.getByText('hello').className).not.toContain(ORANGE)
  })

  it('applies the gap-orange colour to a plain word when fallbackGapColor is set', () => {
    renderWord({ fallbackGapColor: true })
    expect(screen.getByText('hello').className).toContain(ORANGE)
  })

  it('does not override an anchor word (real classification wins)', () => {
    renderWord({ fallbackGapColor: true, isAnchor: true })
    const cls = screen.getByText('hello').className
    expect(cls).toContain(HIGHLIGHT_CLASSES.anchor)
    expect(cls).not.toContain(ORANGE)
  })

  it('does not override a currently-playing word (blue wins)', () => {
    renderWord({ fallbackGapColor: true, isCurrentlyPlaying: true })
    const cls = screen.getByText('hello').className
    expect(cls).toContain('bg-blue-500')
    expect(cls).not.toContain(ORANGE)
  })

  it('does not override a user-edited word', () => {
    renderWord({ fallbackGapColor: true, isUserEdited: true })
    const cls = screen.getByText('hello').className
    expect(cls).toContain(HIGHLIGHT_CLASSES.userEdited)
    expect(cls).not.toContain(ORANGE)
  })
})
