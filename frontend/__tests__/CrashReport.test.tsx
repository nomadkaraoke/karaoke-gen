/**
 * @jest-environment jsdom
 */
import React from 'react'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import messages from '@/messages/en.json'
import CrashReport from '@/components/CrashReport'

jest.mock('@/lib/crash-reporter', () => ({
  reportClientError: jest.fn().mockResolvedValue(undefined),
}))
import { reportClientError } from '@/lib/crash-reporter'

function wrap(ui: React.ReactElement) {
  return (
    <NextIntlClientProvider locale="en" messages={messages as any}>
      {ui}
    </NextIntlClientProvider>
  )
}

describe('CrashReport', () => {
  beforeEach(() => {
    ;(reportClientError as jest.Mock).mockClear()
    ;(reportClientError as jest.Mock).mockResolvedValue(undefined)
  })

  it('renders title and auto-sends on mount', async () => {
    const err = new Error('boom')
    render(wrap(<CrashReport error={err} source="test" />))
    expect(screen.getByText(/Something went wrong/i)).toBeInTheDocument()
    await waitFor(() => expect(reportClientError).toHaveBeenCalled())
    const [[args]] = (reportClientError as jest.Mock).mock.calls
    expect(args.source).toBe('test')
  })

  it('shows a success indicator after successful send', async () => {
    render(wrap(<CrashReport error={new Error('x')} source="test" />))
    expect(await screen.findByText(/Crash report sent/i)).toBeInTheDocument()
  })

  it('copies debug info to clipboard on click', async () => {
    const writeText = jest.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    render(wrap(<CrashReport error={new Error('copy me')} source="test" />))
    fireEvent.click(screen.getByRole('button', { name: /Copy debug info/i }))
    await waitFor(() => expect(writeText).toHaveBeenCalled())
    expect(writeText.mock.calls[0][0]).toContain('copy me')
  })

  it('calls onReset when Try again is clicked', () => {
    const reset = jest.fn()
    render(wrap(<CrashReport error={new Error('x')} source="test" onReset={reset} />))
    fireEvent.click(screen.getByRole('button', { name: /Try again/i }))
    expect(reset).toHaveBeenCalled()
  })
})
