/**
 * @jest-environment jsdom
 */
import React from 'react'
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react'
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

// API client. getMagicLinkStatus decides the initial UI (gate vs. dead-link);
// resendMagicLinkFromToken drives the recovery flow.
const resendMock = jest.fn()
const getStatusMock = jest.fn()
jest.mock('@/lib/api', () => ({
  api: {
    getMagicLinkStatus: (token: string) => getStatusMock(token),
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

/**
 * The verify page gates verification behind an explicit click so that
 * automated email link-scanners (which render the page but never click)
 * cannot burn the single-use token before the real user acts.
 * Every flow that needs a verification result must click through this gate.
 */
async function clickConfirmToVerify() {
  const confirmButton = await screen.findByRole('button', { name: /Complete Sign-?In/i })
  // Wrap in act + await so the async verify() state updates flush inside act.
  await act(async () => {
    fireEvent.click(confirmButton)
  })
}

describe('VerifyMagicLinkPage — scanner-safety gate (valid link)', () => {
  beforeEach(() => {
    pushMock.mockClear()
    resendMock.mockReset()
    verifyMagicLinkMock.mockClear()
    getStatusMock.mockReset()
    getStatusMock.mockResolvedValue({ status: 'valid' })
  })

  it('does NOT verify the token on mount (link-scanners must not burn it)', async () => {
    render(wrap(<VerifyMagicLinkPage />))

    // The confirm prompt is shown for a valid link...
    expect(
      await screen.findByRole('button', { name: /Complete Sign-?In/i }),
    ).toBeInTheDocument()

    // ...the status check is read-only, and no verification happened.
    expect(getStatusMock).toHaveBeenCalledWith(tokenParam)
    expect(verifyMagicLinkMock).not.toHaveBeenCalled()
  })

  it('verifies with the token only after the user clicks Complete Sign-In', async () => {
    render(wrap(<VerifyMagicLinkPage />))

    await clickConfirmToVerify()

    await waitFor(() =>
      expect(verifyMagicLinkMock).toHaveBeenCalledWith(tokenParam),
    )
  })
})

describe('VerifyMagicLinkPage — already-used / expired link', () => {
  beforeEach(() => {
    pushMock.mockClear()
    resendMock.mockReset()
    verifyMagicLinkMock.mockClear()
    getStatusMock.mockReset()
  })

  it('shows the failure UI upfront (no gate, no verify) when the link is already used', async () => {
    getStatusMock.mockResolvedValue({ status: 'used' })

    render(wrap(<VerifyMagicLinkPage />))

    // Failure UI + one-click resend appear without the user clicking anything.
    expect(await screen.findByText('Sign-in failed')).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: /Email me a new link/i }),
    ).toBeInTheDocument()

    // No "Complete Sign-In" gate, and the token was never sent to verify.
    expect(
      screen.queryByRole('button', { name: /Complete Sign-?In/i }),
    ).not.toBeInTheDocument()
    expect(verifyMagicLinkMock).not.toHaveBeenCalled()
  })

  it('falls back to the gate if the status check itself errors (never block a valid user)', async () => {
    getStatusMock.mockRejectedValue(new Error('status endpoint down'))

    render(wrap(<VerifyMagicLinkPage />))

    expect(
      await screen.findByRole('button', { name: /Complete Sign-?In/i }),
    ).toBeInTheDocument()
    expect(verifyMagicLinkMock).not.toHaveBeenCalled()
  })
})

describe('VerifyMagicLinkPage — resend recovery flow', () => {
  beforeEach(() => {
    pushMock.mockClear()
    resendMock.mockReset()
    verifyMagicLinkMock.mockClear()
    getStatusMock.mockReset()
    getStatusMock.mockResolvedValue({ status: 'valid' })
  })

  it('shows "Email me a new link" button after verification fails', async () => {
    render(wrap(<VerifyMagicLinkPage />))

    await clickConfirmToVerify()

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

    await clickConfirmToVerify()

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

    await clickConfirmToVerify()

    fireEvent.click(await screen.findByRole('button', { name: /Email me a new link/i }))

    expect(
      await screen.findByText("We couldn't find that link"),
    ).toBeInTheDocument()
  })

  it('shows an error message when the resend call rejects', async () => {
    resendMock.mockRejectedValue(new Error('network down'))

    render(wrap(<VerifyMagicLinkPage />))

    await clickConfirmToVerify()

    fireEvent.click(await screen.findByRole('button', { name: /Email me a new link/i }))

    await waitFor(() => {
      expect(
        screen.getByText(/Something went wrong on our end/i),
      ).toBeInTheDocument()
    })
  })
})
