/**
 * @jest-environment jsdom
 *
 * Tests for LocaleBadge — the admin-only flag + language pill showing what UI
 * language a user/job was using.
 */

import React from 'react'
import { render, screen } from '@testing-library/react'
import { LocaleBadge, localeDisplayName } from '@/components/admin/locale-badge'

describe('LocaleBadge', () => {
  it('renders the locale code with a flag for a known language', () => {
    render(<LocaleBadge locale="pt" />)
    // Shows the code by default
    expect(screen.getByText(/pt/)).toBeInTheDocument()
    // Language name is in the tooltip
    expect(screen.getByTitle(/Portuguese/i)).toBeInTheDocument()
  })

  it('renders the full language name when showName is set', () => {
    render(<LocaleBadge locale="ja" showName />)
    expect(screen.getByText(/Japanese/i)).toBeInTheDocument()
  })

  it('normalizes a region-qualified locale to its primary subtag', () => {
    render(<LocaleBadge locale="pt-BR" />)
    expect(screen.getByTitle(/Portuguese \(pt\)/i)).toBeInTheDocument()
  })

  it('renders nothing for an empty locale by default', () => {
    const { container } = render(<LocaleBadge locale={null} />)
    expect(container.firstChild).toBeNull()
  })

  it('renders a placeholder for an empty locale when showEmpty is set', () => {
    render(<LocaleBadge locale={undefined} showEmpty />)
    expect(screen.getByText(/—/)).toBeInTheDocument()
  })

  it('localeDisplayName returns a readable name or null', () => {
    expect(localeDisplayName('de')).toMatch(/German/i)
    expect(localeDisplayName(null)).toBeNull()
  })
})
