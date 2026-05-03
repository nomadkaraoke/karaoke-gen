import { render, screen, fireEvent } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import messages from '@/messages/en.json'
import CustomLyricsPreview from '../CustomLyricsPreview'
import type { LineMetadata } from '@/lib/api/customLyrics'

const baseMeta: LineMetadata = {
  line_index: 0,
  target_text: 'aa bb',
  candidate_text: 'xx yy',
  target_syllables: [2, 2, 2, 2],
  candidate_syllables: [2, 2, 2, 2],
  min_delta: 0,
  passes: true,
  severity: 'ok',
  time_budget_seconds: 1.0,
}

function renderWith(props: Partial<React.ComponentProps<typeof CustomLyricsPreview>> = {}) {
  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <CustomLyricsPreview
        lines={['xx yy', 'aa bb']}
        lineMetadata={[
          { ...baseMeta, line_index: 0, candidate_text: 'xx yy' },
          { ...baseMeta, line_index: 1, candidate_text: 'aa bb', target_text: 'cc dd' },
        ]}
        iterationsUsed={0}
        stopReason="success"
        expectedLineCount={2}
        onLineEdit={jest.fn()}
        {...props}
      />
    </NextIntlClientProvider>,
  )
}

describe('CustomLyricsPreview', () => {
  it('renders one row per line', () => {
    renderWith()
    expect(screen.getAllByRole('textbox')).toHaveLength(2)
  })

  it('shows iterations badge', () => {
    renderWith({ iterationsUsed: 3 })
    expect(screen.getByText(/AI iterations: 3/i)).toBeInTheDocument()
  })

  it('shows passing summary', () => {
    renderWith({
      lineMetadata: [
        { ...baseMeta, line_index: 0, severity: 'ok', passes: true },
        { ...baseMeta, line_index: 1, severity: 'major', passes: false, min_delta: 5 },
      ],
    })
    expect(screen.getByText(/1\/2 lines passing/i)).toBeInTheDocument()
  })

  it('shows variable-line-count banner when actual ≠ expected', () => {
    renderWith({ expectedLineCount: 3, lines: ['x', 'y'] })
    expect(screen.getByText(/Output has 2 lines vs 3 original/i)).toBeInTheDocument()
  })

  it('editing a line fires onLineEdit', () => {
    const onLineEdit = jest.fn()
    renderWith({ onLineEdit })
    const inputs = screen.getAllByRole('textbox')
    fireEvent.change(inputs[0], { target: { value: 'new text' } })
    expect(onLineEdit).toHaveBeenCalledWith(0, 'new text')
  })

  it('shows stop-reason message for plateau', () => {
    renderWith({ stopReason: 'plateau' })
    expect(screen.getByText(/AI couldn't improve further/i)).toBeInTheDocument()
  })

  it('labels each input with its line number for accessibility', () => {
    renderWith()
    expect(screen.getByLabelText('Line 1')).toBeInTheDocument()
    expect(screen.getByLabelText('Line 2')).toBeInTheDocument()
  })
})
