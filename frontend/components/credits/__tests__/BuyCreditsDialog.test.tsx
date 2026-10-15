/**
 * BuyCreditsDialog: prices reflect whichever discount checkout will apply
 * (referral vs time-limited conversion-email promo).
 */
import { render, screen } from '@testing-library/react'
import { BuyCreditsDialog } from '../BuyCreditsDialog'
import { useAuth } from '@/lib/auth'
import { api } from '@/lib/api'

jest.mock('@/lib/auth', () => ({ useAuth: jest.fn() }))
jest.mock('@/lib/api', () => ({
  api: { getCreditPackages: jest.fn(), createCheckout: jest.fn() },
  getReferralInterstitial: jest.fn().mockResolvedValue({ referrer_display_name: 'Pat' }),
}))

const mockUseAuth = useAuth as unknown as jest.Mock
const mockPackages = api.getCreditPackages as jest.Mock

const future = new Date(Date.now() + 6 * 3600 * 1000).toISOString()
const past = new Date(Date.now() - 3600 * 1000).toISOString()

function setUser(extra: Record<string, unknown>) {
  mockUseAuth.mockReturnValue({ user: { email: 'fan@example.com', credits: 0, ...extra } })
}

describe('BuyCreditsDialog discounts', () => {
  beforeEach(() => {
    mockPackages.mockResolvedValue([{ id: '1_credit', credits: 1, price_cents: 1000, name: '1 Karaoke Credit' }])
  })

  it('shows the promo banner and promo-discounted price when the promo is the better deal', async () => {
    setUser({ promo_discount_percent: 25, promo_discount_expires_at: future,
      has_active_referral_discount: true, referral_discount_percent: 20, referred_by_code: 'pat' })
    render(<BuyCreditsDialog open onClose={() => {}} />)
    expect(await screen.findByTestId('promo-discount-banner')).toHaveTextContent('25% off')
    expect(await screen.findAllByText(/\$7\.50/)).not.toHaveLength(0)
    expect(screen.queryByText(/referred by/i)).not.toBeInTheDocument()
  })

  it('ignores an expired promo', async () => {
    setUser({ promo_discount_percent: 25, promo_discount_expires_at: past })
    render(<BuyCreditsDialog open onClose={() => {}} />)
    expect(await screen.findAllByText('$10')).not.toHaveLength(0)
    expect(screen.queryByTestId('promo-discount-banner')).not.toBeInTheDocument()
  })

  it('keeps the referral discount when it beats the promo', async () => {
    setUser({ promo_discount_percent: 25, promo_discount_expires_at: future,
      has_active_referral_discount: true, referral_discount_percent: 50, referred_by_code: 'pat' })
    render(<BuyCreditsDialog open onClose={() => {}} />)
    expect(await screen.findAllByText('$5')).not.toHaveLength(0)
    expect(screen.queryByTestId('promo-discount-banner')).not.toBeInTheDocument()
  })
})
