import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { NextIntlClientProvider } from 'next-intl'
import enMessages from '@/messages/en.json'
import SingerChip from '../SingerChip'

function renderChip(props: any) {
  return render(
    <NextIntlClientProvider locale="en" messages={enMessages}>
      <SingerChip {...props} />
    </NextIntlClientProvider>,
  )
}

describe('SingerChip', () => {
  it('renders Singer 1 label by default', () => {
    const onChange = jest.fn()
    renderChip({ singer: 1, hasOverrides: false, onChange })
    expect(screen.getByRole('button')).toHaveTextContent('1')
  })

  it('renders Singer 2 label', () => {
    renderChip({ singer: 2, hasOverrides: false, onChange: jest.fn() })
    expect(screen.getByRole('button')).toHaveTextContent('2')
  })

  it('renders Both label', () => {
    renderChip({ singer: 0, hasOverrides: false, onChange: jest.fn() })
    expect(screen.getByRole('button')).toHaveTextContent(/Both/)
  })

  it('shows asterisk when hasOverrides is true', () => {
    renderChip({ singer: 1, hasOverrides: true, onChange: jest.fn() })
    expect(screen.getByRole('button').textContent).toContain('*')
  })

  it('click cycles through singers', async () => {
    const onChange = jest.fn()
    const user = userEvent.setup()
    renderChip({ singer: 1, hasOverrides: false, onChange })
    await user.click(screen.getByRole('button'))
    expect(onChange).toHaveBeenCalledWith(2)
  })
})
