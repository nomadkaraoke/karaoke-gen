/**
 * @jest-environment jsdom
 *
 * Web account deletion page (Google Play web deletion path).
 */
import React from 'react'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { DeleteAccountClient } from '@/app/[locale]/account/delete/client'

const mockNotFound = jest.fn()
jest.mock('next/navigation', () => ({
  notFound: () => mockNotFound(),
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
}))
jest.mock('@/components/app-header', () => ({ AppHeader: () => null }))
jest.mock('@/components/auth/AuthDialog', () => ({ AuthDialog: () => null }))

const mockLogout = jest.fn().mockResolvedValue(undefined)
jest.mock('@/lib/auth', () => ({
  useAuth: { getState: () => ({ logout: mockLogout }) },
}))

jest.mock('@/lib/api', () => ({
  getAccessToken: jest.fn(),
  api: {
    getCurrentUser: jest.fn(),
    getDeletionPreview: jest.fn(),
    deleteAccount: jest.fn(),
  },
}))

import { api, getAccessToken } from '@/lib/api'

const getCurrentUser = api.getCurrentUser as jest.Mock
const getDeletionPreview = api.getDeletionPreview as jest.Mock
const deleteAccount = api.deleteAccount as jest.Mock

const apiError = (status: number, data?: unknown) => Object.assign(new Error('x'), { status, data })

async function startDeletion() {
  await screen.findByTestId('delete-account-start')
  fireEvent.click(screen.getByTestId('delete-account-start'))
  fireEvent.click(screen.getByTestId('delete-account-confirm'))
}

describe('DeleteAccountClient', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    ;(getAccessToken as jest.Mock).mockReturnValue('tok')
    getCurrentUser.mockResolvedValue({ user: {}, has_session: true, capabilities: { account_deletion: true } })
    getDeletionPreview.mockResolvedValue({ credits: 3, jobs_in_progress: 2, blocked: null })
  })

  it('renders preview counts and the kept/deleted copy', async () => {
    render(<DeleteAccountClient />)
    expect(await screen.findByTestId('delete-account-credits')).toHaveTextContent('3 credits will be lost')
    expect(screen.getByTestId('delete-account-jobs')).toHaveTextContent('2 videos in progress will be cancelled')
    expect(screen.getByText(/kept for accounting/i)).toBeInTheDocument()
    expect(screen.getByText(/published on YouTube/i)).toBeInTheDocument()
    expect(screen.getByText(/song-discovery service/i)).toBeInTheDocument()
    expect(screen.getByText(/Nomad night links/i)).toBeInTheDocument()
  })

  it('confirm calls DELETE, signs out and shows the deleted state', async () => {
    deleteAccount.mockResolvedValue({ deleted: true, summary: {} })
    render(<DeleteAccountClient />)
    await startDeletion()
    expect(await screen.findByTestId('delete-account-deleted')).toBeInTheDocument()
    expect(deleteAccount).toHaveBeenCalledTimes(1)
    expect(mockLogout).toHaveBeenCalled()
  })

  it('does not delete until the user confirms', async () => {
    render(<DeleteAccountClient />)
    fireEvent.click(await screen.findByTestId('delete-account-start'))
    expect(deleteAccount).not.toHaveBeenCalled()
    fireEvent.click(screen.getByTestId('delete-account-cancel'))
    expect(screen.getByTestId('delete-account-start')).toBeInTheDocument()
  })

  it('treats 401 on confirm as already deleted', async () => {
    deleteAccount.mockRejectedValue(apiError(401))
    render(<DeleteAccountClient />)
    await startDeletion()
    expect(await screen.findByTestId('delete-account-deleted')).toBeInTheDocument()
    expect(mockLogout).toHaveBeenCalled()
  })

  it('shows support copy on 409', async () => {
    deleteAccount.mockRejectedValue(apiError(409, { code: 'referral_balance' }))
    render(<DeleteAccountClient />)
    await startDeletion()
    const blocked = await screen.findByTestId('delete-account-blocked')
    expect(blocked).toHaveTextContent('support@nomadkaraoke.com')
    expect(blocked).toHaveTextContent(/referral balance/i)
    expect(mockLogout).not.toHaveBeenCalled()
  })

  it('shows support copy when the preview is already blocked', async () => {
    getDeletionPreview.mockResolvedValue({ credits: 0, jobs_in_progress: 0, blocked: 'admin' })
    render(<DeleteAccountClient />)
    const blocked = await screen.findByTestId('delete-account-blocked')
    expect(blocked).toHaveTextContent('support@nomadkaraoke.com')
    expect(screen.queryByTestId('delete-account-start')).not.toBeInTheDocument()
  })

  it('offers retry on 503 and does not sign out', async () => {
    deleteAccount.mockRejectedValueOnce(apiError(503, { step: 'decide' }))
    render(<DeleteAccountClient />)
    await startDeletion()
    const retry = await screen.findByTestId('delete-account-retry')
    expect(mockLogout).not.toHaveBeenCalled()
    deleteAccount.mockResolvedValue({ deleted: true })
    fireEvent.click(retry)
    await waitFor(() => expect(deleteAccount).toHaveBeenCalledTimes(2))
    expect(await screen.findByTestId('delete-account-deleted')).toBeInTheDocument()
  })

  it('is a 404 when the capability is false', async () => {
    getCurrentUser.mockResolvedValue({ user: {}, has_session: true, capabilities: { account_deletion: false } })
    render(<DeleteAccountClient />)
    await waitFor(() => expect(mockNotFound).toHaveBeenCalled())
    expect(getDeletionPreview).not.toHaveBeenCalled()
    expect(screen.queryByTestId('delete-account-start')).not.toBeInTheDocument()
  })

  it('asks signed-out visitors to sign in', async () => {
    ;(getAccessToken as jest.Mock).mockReturnValue(null)
    render(<DeleteAccountClient />)
    expect(await screen.findByTestId('delete-account-signed-out')).toBeInTheDocument()
    expect(getCurrentUser).not.toHaveBeenCalled()
  })
})
