/**
 * @jest-environment jsdom
 *
 * Tests for CommunityVersions — the vertical, clickable list of existing
 * community karaoke versions shown under a bulk-mode track. next-intl is
 * globally mocked in jest.setup.js (real 'bulk' strings).
 */

import React from 'react'
import { render, screen } from '@testing-library/react'
import { CommunityVersions } from '@/components/job/bulk/CommunityVersions'

describe('CommunityVersions', () => {
  const versions = [
    { brand: 'SNDL Karaoke', url: 'https://www.youtube.com/watch?v=aaa' },
    { brand: 'Nomad Karaoke', url: 'https://www.youtube.com/watch?v=bbb' },
  ]

  it('renders a clickable YouTube link per version, opening in a new tab', () => {
    render(<CommunityVersions available versions={versions} />)
    expect(screen.getByText(/existing versions/i)).toBeInTheDocument()

    const sndl = screen.getByRole('link', { name: /SNDL Karaoke/ })
    expect(sndl).toHaveAttribute('href', 'https://www.youtube.com/watch?v=aaa')
    expect(sndl).toHaveAttribute('target', '_blank')
    expect(sndl).toHaveAttribute('rel', expect.stringContaining('noopener'))

    const nomad = screen.getByRole('link', { name: /Nomad Karaoke/ })
    expect(nomad).toHaveAttribute('href', 'https://www.youtube.com/watch?v=bbb')

    expect(screen.getAllByRole('link')).toHaveLength(2)
  })

  it('shows a plain "exists" line when available but no versions have URLs', () => {
    render(<CommunityVersions available versions={[]} />)
    expect(screen.getByText(/community version exists/i)).toBeInTheDocument()
    expect(screen.queryAllByRole('link')).toHaveLength(0)
  })

  it('renders nothing when the track is not already available', () => {
    const { container } = render(<CommunityVersions available={false} versions={versions} />)
    expect(container.firstChild).toBeNull()
  })
})
