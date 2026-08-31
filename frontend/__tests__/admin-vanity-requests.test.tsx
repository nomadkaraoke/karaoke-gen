/**
 * @jest-environment jsdom
 *
 * Tests for the admin Referrals page "Pending Vanity Requests" banner —
 * approving/denying a user-requested vanity code. adminApi is mocked.
 */

import React from 'react'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'

const listReferralLinks = jest.fn()
const listVanityRequests = jest.fn()
const approveVanityRequest = jest.fn()
const denyVanityRequest = jest.fn()

// ReferralToolsDialog pulls in jspdf (ESM) which jest can't transform — stub it.
jest.mock('@/components/referrals/ReferralToolsDialog', () => ({
  __esModule: true,
  default: () => null,
}))

jest.mock('@/lib/api', () => ({
  adminApi: {
    listReferralLinks: (...args: unknown[]) => listReferralLinks(...args),
    listVanityRequests: (...args: unknown[]) => listVanityRequests(...args),
    approveVanityRequest: (...args: unknown[]) => approveVanityRequest(...args),
    denyVanityRequest: (...args: unknown[]) => denyVanityRequest(...args),
    createVanityLink: jest.fn(),
    updateReferralLink: jest.fn(),
  },
}))

import ReferralsPage from '@/app/admin/referrals/page'

const pendingRequest = {
  id: 'youtube@nomadkaraoke.com',
  owner_email: 'youtube@nomadkaraoke.com',
  current_code: 'odu4brd8',
  desired_code: 'youtube',
  status: 'pending' as const,
  created_at: '2026-08-30T00:00:00Z',
}

beforeEach(() => {
  jest.clearAllMocks()
  listReferralLinks.mockResolvedValue({ links: [], count: 0 })
  listVanityRequests.mockResolvedValue({ requests: [pendingRequest], count: 1 })
  approveVanityRequest.mockResolvedValue({ ok: true, code: 'youtube', message: 'Renamed' })
  denyVanityRequest.mockResolvedValue({ ok: true, message: 'Request denied' })
})

describe('Admin Referrals — pending vanity requests', () => {
  it('shows the pending request with current → desired codes', async () => {
    render(<ReferralsPage />)
    expect(await screen.findByText('Pending Vanity Requests')).toBeInTheDocument()
    expect(screen.getByText('youtube@nomadkaraoke.com')).toBeInTheDocument()
    expect(screen.getByText('odu4brd8')).toBeInTheDocument()
    expect(screen.getByText('youtube')).toBeInTheDocument()
  })

  it('approving calls approveVanityRequest and reloads', async () => {
    render(<ReferralsPage />)
    const approveBtn = await screen.findByRole('button', { name: /approve/i })
    fireEvent.click(approveBtn)
    await waitFor(() =>
      expect(approveVanityRequest).toHaveBeenCalledWith('youtube@nomadkaraoke.com')
    )
    // Data reloaded (initial load + post-approve reload)
    await waitFor(() => expect(listVanityRequests).toHaveBeenCalledTimes(2))
  })

  it('denying calls denyVanityRequest', async () => {
    render(<ReferralsPage />)
    const denyBtn = await screen.findByRole('button', { name: /deny/i })
    fireEvent.click(denyBtn)
    await waitFor(() =>
      expect(denyVanityRequest).toHaveBeenCalledWith('youtube@nomadkaraoke.com')
    )
  })

  it('renders no banner when there are no pending requests', async () => {
    listVanityRequests.mockResolvedValue({ requests: [], count: 0 })
    render(<ReferralsPage />)
    await waitFor(() => expect(listReferralLinks).toHaveBeenCalled())
    expect(screen.queryByText('Pending Vanity Requests')).not.toBeInTheDocument()
  })
})
