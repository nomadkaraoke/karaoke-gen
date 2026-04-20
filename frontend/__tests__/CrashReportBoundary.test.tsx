/**
 * @jest-environment jsdom
 */
import React from 'react'
import { render, screen } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import messages from '@/messages/en.json'
import CrashReportBoundary from '@/components/CrashReportBoundary'

jest.mock('@/lib/crash-reporter', () => ({
  reportClientError: jest.fn().mockResolvedValue(undefined),
}))

function Bomb(): React.ReactElement {
  throw new Error('KABOOM')
}

function wrap(ui: React.ReactElement) {
  return (
    <NextIntlClientProvider locale="en" messages={messages as any}>
      {ui}
    </NextIntlClientProvider>
  )
}

describe('CrashReportBoundary', () => {
  const origError = console.error
  beforeAll(() => {
    // Silence React's expected error noise in tests
    console.error = jest.fn()
  })
  afterAll(() => {
    console.error = origError
  })

  it('catches a child error and renders CrashReport', () => {
    render(wrap(
      <CrashReportBoundary source="test">
        <Bomb />
      </CrashReportBoundary>
    ))
    expect(screen.getByText(/Something went wrong/i)).toBeInTheDocument()
    expect(screen.getByTestId('crash-report-message').textContent).toContain('KABOOM')
  })

  it('renders children normally when no error', () => {
    render(wrap(
      <CrashReportBoundary source="test">
        <div>hello</div>
      </CrashReportBoundary>
    ))
    expect(screen.getByText('hello')).toBeInTheDocument()
  })
})
