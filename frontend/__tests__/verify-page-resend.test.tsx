/**
 * @jest-environment jsdom
 */
import React from 'react'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { NextIntlClientProvider } from 'next-intl'
import messages from '@/messages/en.json'

// Stub Next router + searchParams hooks so the page renders in isolation.
const pushMock = jest.fn()
jest.mock('@/i18n/routing', () => ({
  useRouter: () => ({ push: pushMock }),
  Link: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))

const tokenParam = 'expired-original-token'
jest.mock('next/navigation', () => ({
  useSearchParams: () => ({
    get: (key: string) => (key === 'token' ? tokenParam : null),
  }),
}))

// useAuth: we want the verify call to fail so the page lands in the error state.
const verifyMagicLinkMock = jest.fn().mockResolvedValue(false)
jest.mock('@/lib/auth', () => {
  const useAuthFn = () => ({
    verifyMagicLink: verifyMagicLinkMock,
    user: null,
    error: null,
  })
  ;(useAuthFn as unknown as { getState: () => { error: string } }).getState = () => ({
    error: 'Link expired',
  })
  return { useAuth: useAuthFn }
})

// Referral helper is incidental.
jest.mock('@/lib/referral', () => ({ setReferralCode: jest.fn() }))

// API client — only the resend method matters here.
const resendMock = jest.fn()
jest.mock('@/lib/api', () => ({
  api: {
    resendMagicLinkFromToken: (token: string) => resendMock(token),
  },
}))

// Import AFTER mocks are wired.
import VerifyMagicLinkPage from '@/app/[locale]/auth/verify/page'

function wrap(ui: React.ReactElement) {
  return (
    <NextIntlClientProvider locale="en" messages={messages as any}>
      {ui}
    </NextIntlClientProvider>
  )
}

describe('VerifyMagicLinkPage — resend recovery flow', () => {
  beforeEach(() => {
    pushMock.mockClear()
    resendMock.mockReset()
    verifyMagicLinkMock.mockClear()
  })

  it('shows "Email me a new link" button after verification fails', async () => {
    render(wrap(<VerifyMagicLinkPage />))

    await waitFor(() => {
      expect(screen.getByText('Sign-in failed')).toBeInTheDocument()
    })

    expect(
      screen.getByRole('button', { name: /Email me a new link/i }),
    ).toBeInTheDocument()
  })

  it('clicking the resend button calls the API with the original token and shows success', async () => {
    resendMock.mockResolvedValue({
      status: 'sent',
      masked_email: 'ho***@ya***.com',
      message: 'sent',
    })

    render(wrap(<VerifyMagicLinkPage />))

    const button = await screen.findByRole('button', { name: /Email me a new link/i })
    fireEvent.click(button)

    await waitFor(() => expect(resendMock).toHaveBeenCalledWith(tokenParam))

    expect(await screen.findByText('Check your email')).toBeInTheDocument()
    expect(screen.getByText(/ho\*\*\*@ya\*\*\*\.com/)).toBeInTheDocument()
  })

  it('shows fallback message when backend reports no_token', async () => {
    resendMock.mockResolvedValue({
      status: 'no_token',
      masked_email: null,
      message: 'no token',
    })

    render(wrap(<VerifyMagicLinkPage />))

    fireEvent.click(await screen.findByRole('button', { name: /Email me a new link/i }))

    expect(
      await screen.findByText("We couldn't find that link"),
    ).toBeInTheDocument()
  })

  it('shows an error message when the resend call rejects', async () => {
    resendMock.mockRejectedValue(new Error('network down'))

    render(wrap(<VerifyMagicLinkPage />))

    fireEvent.click(await screen.findByRole('button', { name: /Email me a new link/i }))

    await waitFor(() => {
      expect(
        screen.getByText(/Something went wrong on our end/i),
      ).toBeInTheDocument()
    })
  })
})
