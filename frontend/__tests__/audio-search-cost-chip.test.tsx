/**
 * @jest-environment jsdom
 *
 * Tests for ResultCostChip — the small credit-cost badge rendered next to
 * each audio-search result that has a known duration.
 *
 * next-intl is globally mocked in jest.setup.js. The 'audioSearch' namespace
 * IS in en.json, so t('creditCost', { credits: 3 }) → "3 credits" (real string).
 */

import React from 'react'
import { render, screen } from '@testing-library/react'
import { ResultCostChip } from '@/components/audio-search/ResultCostChip'

describe('ResultCostChip', () => {
  it('renders credit count for a result with duration=1500 (→ 3 credits)', () => {
    render(<ResultCostChip durationSeconds={1500} />)
    // 1500s → ceil(1500/600) = 3 credits
    expect(screen.getByText(/3/)).toBeInTheDocument()
    expect(screen.getByText(/credits/i)).toBeInTheDocument()
  })

  it('renders "1 credit" for a short result (duration=300, → 1 credit)', () => {
    render(<ResultCostChip durationSeconds={300} />)
    expect(screen.getByText(/1/)).toBeInTheDocument()
    expect(screen.getByText(/credits/i)).toBeInTheDocument()
  })

  it('renders nothing when durationSeconds is undefined', () => {
    const { container } = render(<ResultCostChip durationSeconds={undefined} />)
    expect(container.firstChild).toBeNull()
  })

  it('renders nothing when durationSeconds is null', () => {
    const { container } = render(<ResultCostChip durationSeconds={null} />)
    expect(container.firstChild).toBeNull()
  })

  it('applies warning style for over-60-min duration (→ isBlocked)', () => {
    // 3700s > 3600s → isBlocked = true → warning chip
    render(<ResultCostChip durationSeconds={3700} />)
    const chip = screen.getByTestId('result-cost-chip')
    expect(chip).toHaveClass('text-orange-400')
  })

  it('applies normal style for exactly 60 min (3600s, not blocked)', () => {
    render(<ResultCostChip durationSeconds={3600} />)
    const chip = screen.getByTestId('result-cost-chip')
    expect(chip).not.toHaveClass('text-orange-400')
  })
})
