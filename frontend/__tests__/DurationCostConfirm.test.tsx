/**
 * @jest-environment jsdom
 *
 * Tests for DurationCostConfirm modal.
 *
 * next-intl is globally mocked in jest.setup.js to read from messages/en.json.
 * The 'pricing' namespace exists in en.json, so useTranslations('pricing') returns
 * real English strings with ICU param interpolation (e.g. t('confirm') → 'Confirm & start').
 *
 * The component uses the shared Radix Dialog primitive (via @/components/ui/dialog),
 * so the dialog is rendered in a portal. Use screen.getByRole('dialog') to find it.
 */

import React from 'react'
import { render, screen, fireEvent } from '@testing-library/react'
import { DurationCostConfirm } from '@/components/job/DurationCostConfirm'

const baseProps = {
  open: true,
  durationSeconds: 905,     // 905s → ceil(905/60) = 16 minutes
  credits: 2,
  balance: 5,               // affordable: 5 >= 2
  onConfirm: jest.fn(),
  onClose: jest.fn(),
  onBuyCredits: jest.fn(),
}

beforeEach(() => {
  jest.clearAllMocks()
})

describe('DurationCostConfirm', () => {
  it('does not render dialog when open is false', () => {
    render(<DurationCostConfirm {...baseProps} open={false} />)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('renders dialog with role="dialog" when open', () => {
    render(<DurationCostConfirm {...baseProps} />)
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })

  it('renders duration in minutes (905s → 16 minutes visible in text)', () => {
    render(<DurationCostConfirm {...baseProps} />)
    // t('creditsForDuration', {minutes: 16, credits: 2}) → "16 min · 2 credits"
    // The jest.setup.js mock reads from en.json and interpolates ICU params.
    expect(screen.getByText(/16 min/)).toBeInTheDocument()
    expect(screen.getByText(/2 credits/)).toBeInTheDocument()
  })

  it('shows Confirm button when balance >= credits and calls onConfirm when clicked', () => {
    const onConfirm = jest.fn()
    render(<DurationCostConfirm {...baseProps} balance={5} credits={2} onConfirm={onConfirm} />)
    // t('confirm') → "Confirm & start"
    const confirmBtn = screen.getByRole('button', { name: /confirm & start/i })
    expect(confirmBtn).toBeInTheDocument()
    fireEvent.click(confirmBtn)
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })

  it('does NOT show Buy Credits button when balance >= credits', () => {
    render(<DurationCostConfirm {...baseProps} balance={5} credits={2} />)
    // t('buyCredits') → "Buy credits"
    expect(screen.queryByRole('button', { name: /buy credits/i })).not.toBeInTheDocument()
  })

  it('shows Buy Credits button when balance < credits and calls onBuyCredits when clicked', () => {
    const onBuyCredits = jest.fn()
    render(
      <DurationCostConfirm
        {...baseProps}
        balance={1}
        credits={2}
        onBuyCredits={onBuyCredits}
      />
    )
    // t('buyCredits') → "Buy credits"
    const buyBtn = screen.getByRole('button', { name: /buy credits/i })
    expect(buyBtn).toBeInTheDocument()
    fireEvent.click(buyBtn)
    expect(onBuyCredits).toHaveBeenCalledTimes(1)
  })

  it('does NOT show Confirm button when balance < credits', () => {
    render(<DurationCostConfirm {...baseProps} balance={1} credits={2} />)
    // t('confirm') → "Confirm & start" — should not be present
    expect(screen.queryByRole('button', { name: /confirm & start/i })).not.toBeInTheDocument()
  })

  it('calls onClose when cancel button is clicked', () => {
    const onClose = jest.fn()
    render(<DurationCostConfirm {...baseProps} onClose={onClose} />)
    // t('cancel') → "Cancel"
    const cancelBtn = screen.getByRole('button', { name: /^cancel$/i })
    fireEvent.click(cancelBtn)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('uses reconcile title copy when reconcile=true', () => {
    render(<DurationCostConfirm {...baseProps} reconcile={true} />)
    // t('reconcileTitle') → "This song is longer than expected"
    expect(screen.getByText('This song is longer than expected')).toBeInTheDocument()
    expect(screen.queryByText('Confirm your karaoke job')).not.toBeInTheDocument()
  })

  it('uses confirm title copy when reconcile=false (default)', () => {
    render(<DurationCostConfirm {...baseProps} reconcile={false} />)
    // t('confirmTitle') → "Confirm your karaoke job"
    expect(screen.getByText('Confirm your karaoke job')).toBeInTheDocument()
    expect(screen.queryByText('This song is longer than expected')).not.toBeInTheDocument()
  })

  it('shows estimated label when estimated=true', () => {
    render(<DurationCostConfirm {...baseProps} estimated={true} />)
    // t('estimatedLabel') → "Estimated — final cost confirmed after download"
    expect(screen.getByText('Estimated — final cost confirmed after download')).toBeInTheDocument()
  })

  it('does not show estimated label when estimated=false (default)', () => {
    render(<DurationCostConfirm {...baseProps} estimated={false} />)
    expect(screen.queryByText('Estimated — final cost confirmed after download')).not.toBeInTheDocument()
  })

  it('shows Confirm (not Buy Credits) when balance === credits (boundary)', () => {
    render(<DurationCostConfirm {...baseProps} balance={2} credits={2} />)
    expect(screen.getByRole('button', { name: /confirm & start/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /buy credits/i })).not.toBeInTheDocument()
  })

  it('Buy Credits button is disabled when onBuyCredits is not provided', () => {
    // Omit onBuyCredits — balance < credits so the button renders but should be disabled
    const { onBuyCredits: _omit, ...propsWithoutBuyCredits } = baseProps
    render(
      <DurationCostConfirm
        {...propsWithoutBuyCredits}
        balance={1}
        credits={2}
      />
    )
    // t('buyCredits') → "Buy credits"
    const buyBtn = screen.getByRole('button', { name: /buy credits/i })
    expect(buyBtn).toBeDisabled()
  })
})
