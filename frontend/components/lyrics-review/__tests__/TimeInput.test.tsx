/**
 * Tests for TimeInput component
 *
 * Tests the debounced time input behavior for editing segment times.
 */

import { render, screen, fireEvent, act, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import TimeInput from '../TimeInput'

// Mock timers for debounce testing
jest.useFakeTimers()

describe('TimeInput', () => {
  const mockOnChange = jest.fn()

  beforeEach(() => {
    jest.clearAllMocks()
  })

  afterEach(() => {
    jest.clearAllTimers()
  })

  describe('display', () => {
    it('displays formatted value when not focused', () => {
      render(<TimeInput value={12.345} onChange={mockOnChange} label="Start" />)

      const input = screen.getByRole('spinbutton')
      expect(input).toHaveValue(12.35) // toFixed(2) rounds
    })

    it('displays empty string for null value', () => {
      render(<TimeInput value={null} onChange={mockOnChange} label="Start" />)

      const input = screen.getByRole('spinbutton')
      expect(input).toHaveValue(null)
    })

    it('displays the label', () => {
      render(<TimeInput value={1.5} onChange={mockOnChange} label="End Time" />)

      expect(screen.getByText('End Time')).toBeInTheDocument()
    })

    it('applies custom width class', () => {
      const { container } = render(
        <TimeInput value={1.5} onChange={mockOnChange} label="Start" widthClass="w-20" />
      )

      const wrapper = container.firstChild
      expect(wrapper).toHaveClass('w-20')
    })
  })

  describe('typing behavior', () => {
    it('allows typing without immediate onChange', async () => {
      const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
      render(<TimeInput value={1.5} onChange={mockOnChange} label="Start" />)

      const input = screen.getByRole('spinbutton')
      await user.click(input)
      await user.clear(input)
      await user.type(input, '12')

      // onChange should not be called immediately
      expect(mockOnChange).not.toHaveBeenCalled()
    })

    it('selects text on focus', async () => {
      render(<TimeInput value={1.5} onChange={mockOnChange} label="Start" />)

      const input = screen.getByRole('spinbutton') as HTMLInputElement
      fireEvent.focus(input)

      // After focus, input should have the value selected (we can't easily test selection,
      // but we can verify the value is set correctly)
      expect(input.value).toBe('1.50')
    })

    it('maintains local value while focused', async () => {
      const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
      const { rerender } = render(<TimeInput value={1.5} onChange={mockOnChange} label="Start" />)

      const input = screen.getByRole('spinbutton')
      await user.click(input)
      await user.clear(input)
      await user.type(input, '22.33')

      // Simulate parent update (shouldn't affect local state while focused)
      rerender(<TimeInput value={5.0} onChange={mockOnChange} label="Start" />)

      // Should still show what user typed, not the new parent value
      expect(input).toHaveValue(22.33)
    })
  })

  describe('debounce', () => {
    it('calls onChange after debounce delay', async () => {
      const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
      render(<TimeInput value={1.5} onChange={mockOnChange} label="Start" debounceMs={300} />)

      const input = screen.getByRole('spinbutton')
      await user.click(input)
      await user.clear(input)
      await user.type(input, '5')

      expect(mockOnChange).not.toHaveBeenCalled()

      // Advance past debounce
      act(() => {
        jest.advanceTimersByTime(300)
      })

      expect(mockOnChange).toHaveBeenCalledWith(5)
    })

    it('resets debounce timer on each keystroke', async () => {
      const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
      render(<TimeInput value={1.5} onChange={mockOnChange} label="Start" debounceMs={300} />)

      const input = screen.getByRole('spinbutton')
      await user.click(input)
      await user.clear(input)

      // Type first character
      await user.type(input, '1')

      // Wait 200ms (less than debounce)
      act(() => {
        jest.advanceTimersByTime(200)
      })
      expect(mockOnChange).not.toHaveBeenCalled()

      // Type another character (resets debounce)
      await user.type(input, '2')

      // Wait another 200ms (less than debounce from last keystroke)
      act(() => {
        jest.advanceTimersByTime(200)
      })
      expect(mockOnChange).not.toHaveBeenCalled()

      // Wait the remaining time
      act(() => {
        jest.advanceTimersByTime(100)
      })

      expect(mockOnChange).toHaveBeenCalledTimes(1)
      expect(mockOnChange).toHaveBeenCalledWith(12)
    })
  })

  describe('blur behavior', () => {
    it('syncs value immediately on blur', async () => {
      const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
      render(<TimeInput value={1.5} onChange={mockOnChange} label="Start" debounceMs={300} />)

      const input = screen.getByRole('spinbutton')
      await user.click(input)
      await user.clear(input)
      await user.type(input, '12.34')

      // Blur before debounce fires
      fireEvent.blur(input)

      // Should call onChange immediately
      expect(mockOnChange).toHaveBeenCalledWith(12.34)
    })

    it('cancels pending debounce on blur', async () => {
      const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
      render(<TimeInput value={1.5} onChange={mockOnChange} label="Start" debounceMs={300} />)

      const input = screen.getByRole('spinbutton')
      await user.click(input)
      await user.clear(input)
      await user.type(input, '12.34')

      fireEvent.blur(input)

      // Wait for debounce time to pass
      act(() => {
        jest.advanceTimersByTime(300)
      })

      // Should only have been called once (on blur)
      expect(mockOnChange).toHaveBeenCalledTimes(1)
    })

    it('formats display after blur', async () => {
      const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
      render(<TimeInput value={1.5} onChange={mockOnChange} label="Start" />)

      const input = screen.getByRole('spinbutton')
      await user.click(input)
      await user.clear(input)
      await user.type(input, '12.3456')

      fireEvent.blur(input)

      // Verify onChange was called with the typed numeric value
      expect(mockOnChange).toHaveBeenCalledWith(12.3456)

      // After blur, component shows the formatted parent value (still 1.5 until parent updates)
      // In real usage, parent would update to 12.35, but in this test it remains 1.5
      expect(input).toHaveValue(1.5) // Displays parent's value formatted as 1.50
    })

    it('returns null for invalid input on blur', async () => {
      const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
      render(<TimeInput value={1.5} onChange={mockOnChange} label="Start" />)

      const input = screen.getByRole('spinbutton')
      await user.click(input)
      await user.clear(input)
      await user.type(input, 'abc')

      fireEvent.blur(input)

      expect(mockOnChange).toHaveBeenCalledWith(null)
    })
  })

  describe('external updates', () => {
    it('updates display when parent value changes while not focused', () => {
      const { rerender } = render(<TimeInput value={1.5} onChange={mockOnChange} label="Start" />)

      const input = screen.getByRole('spinbutton')
      expect(input).toHaveValue(1.5)

      rerender(<TimeInput value={5.0} onChange={mockOnChange} label="Start" />)

      expect(input).toHaveValue(5)
    })

    it('ignores parent updates while focused', async () => {
      const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
      const { rerender } = render(<TimeInput value={1.5} onChange={mockOnChange} label="Start" />)

      const input = screen.getByRole('spinbutton')
      await user.click(input)
      await user.clear(input)
      await user.type(input, '99')

      // Parent tries to update value
      rerender(<TimeInput value={5.0} onChange={mockOnChange} label="Start" />)

      // Should still show user's input
      expect(input).toHaveValue(99)
    })
  })
})
