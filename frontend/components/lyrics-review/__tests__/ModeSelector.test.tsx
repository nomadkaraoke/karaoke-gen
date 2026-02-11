import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ModeSelector from '../ModeSelector'
import { InteractionMode } from '@/lib/lyrics-review/types'

describe('ModeSelector', () => {
  it('renders all three mode buttons', () => {
    const mockOnChange = jest.fn()
    render(<ModeSelector effectiveMode="edit" onChange={mockOnChange} />)

    expect(screen.getByRole('radio', { name: /edit/i })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /highlight/i })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /delete/i })).toBeInTheDocument()
  })

  it('all mode buttons are enabled (not disabled)', () => {
    const mockOnChange = jest.fn()
    render(<ModeSelector effectiveMode="edit" onChange={mockOnChange} />)

    const editBtn = screen.getByRole('radio', { name: /edit/i })
    const highlightBtn = screen.getByRole('radio', { name: /highlight/i })
    const deleteBtn = screen.getByRole('radio', { name: /delete/i })

    // Verify buttons are not disabled
    expect(editBtn).not.toBeDisabled()
    expect(highlightBtn).not.toBeDisabled()
    expect(deleteBtn).not.toBeDisabled()
  })

  it('calls onChange when clicking highlight button', async () => {
    const user = userEvent.setup()
    const mockOnChange = jest.fn()
    render(<ModeSelector effectiveMode="edit" onChange={mockOnChange} />)

    const highlightBtn = screen.getByRole('radio', { name: /highlight/i })
    await user.click(highlightBtn)

    expect(mockOnChange).toHaveBeenCalledWith('highlight')
  })

  it('calls onChange when clicking delete button', async () => {
    const user = userEvent.setup()
    const mockOnChange = jest.fn()
    render(<ModeSelector effectiveMode="edit" onChange={mockOnChange} />)

    const deleteBtn = screen.getByRole('radio', { name: /delete/i })
    await user.click(deleteBtn)

    expect(mockOnChange).toHaveBeenCalledWith('delete_word')
  })

  it('shows correct active state for current mode', () => {
    const mockOnChange = jest.fn()
    const { rerender } = render(<ModeSelector effectiveMode="highlight" onChange={mockOnChange} />)

    const highlightBtn = screen.getByRole('radio', { name: /highlight/i })
    expect(highlightBtn).toHaveAttribute('aria-checked', 'true')

    rerender(<ModeSelector effectiveMode="delete_word" onChange={mockOnChange} />)
    const deleteBtn = screen.getByRole('radio', { name: /delete/i })
    expect(deleteBtn).toHaveAttribute('aria-checked', 'true')
  })
})
