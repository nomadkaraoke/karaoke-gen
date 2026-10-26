/**
 * @jest-environment jsdom
 *
 * Requests board client: the daily vote can't be spent by accident (confirmation first),
 * submitting is blocked once today's vote is spent, removing the last up-vote warns that
 * the request will be deleted, and a request/vote ends in a thank-you with the credit-pack
 * pitch. next-intl is globally mocked in jest.setup.js (real English strings).
 */
import React from 'react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

const push = jest.fn()
jest.mock('@/i18n/routing', () => ({ useRouter: () => ({ push }) }))
jest.mock('@/components/LanguageSwitcher', () => () => null)
jest.mock('@/components/job/CommunityVersionBanner', () => ({ CommunityVersionBanner: () => null }))
jest.mock('@/lib/auth', () => ({
  useAuth: () => ({ user: { email: 'fan@example.com' }, sendMagicLink: jest.fn(), fetchUser: jest.fn() }),
}))
jest.mock('@/lib/api', () => ({
  getAccessToken: () => 'tok',
  api: {
    getRequestsBoard: jest.fn(),
    submitSongRequest: jest.fn(),
    voteSongRequest: jest.fn(),
    checkCommunityVersions: jest.fn().mockResolvedValue(null),
    claimWelcomeCredit: jest.fn(),
  },
}))

import { api } from '@/lib/api'
import { ApiError } from '@/lib/api-error'
import { RequestsBoardClient } from '@/app/[locale]/requests/client'

const mockApi = api as unknown as Record<string, jest.Mock>

const req = (over: Record<string, unknown> = {}) => ({
  id: 'r1', artist: 'The Lanterns', title: 'Paper Moons', status: 'open', source: 'human',
  vote_count: 1, created_at: '2026-10-26T00:00:00Z', was_corrected: false, your_vote: null, ...over,
})

const board = (over: Record<string, unknown> = {}) => ({
  requests: [req()], published: [], voted_today: false, your_vote_request_id: null, ...over,
})

beforeEach(() => {
  jest.clearAllMocks()
  mockApi.checkCommunityVersions.mockResolvedValue(null)
})

describe('RequestsBoardClient', () => {
  it('asks before spending the daily vote, then thanks and pitches credit packs', async () => {
    mockApi.getRequestsBoard.mockResolvedValue(board())
    mockApi.voteSongRequest.mockResolvedValue(req({ your_vote: 1, vote_count: 2 }))
    render(<RequestsBoardClient />)

    fireEvent.click(await screen.findByLabelText('upvote'))
    expect(screen.getByRole('dialog')).toHaveTextContent(/one vote per day/i)
    expect(mockApi.voteSongRequest).not.toHaveBeenCalled()  // nothing is spent until confirmed

    fireEvent.click(screen.getByRole('button', { name: /yes, do it/i }))
    await waitFor(() => expect(mockApi.voteSongRequest).toHaveBeenCalledWith('r1', 'up', false))
    expect(await screen.findByTestId('thanks-panel')).toHaveTextContent(/credit pack/i)

    fireEvent.click(screen.getByRole('button', { name: /get a credit pack/i }))
    expect(push).toHaveBeenCalledWith('/app?buy=1')
  })

  it('cancelling the confirmation spends nothing', async () => {
    mockApi.getRequestsBoard.mockResolvedValue(board())
    render(<RequestsBoardClient />)
    fireEvent.click(await screen.findByLabelText('upvote'))
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(mockApi.voteSongRequest).not.toHaveBeenCalled()
  })

  it('warns that removing the last up-vote deletes the request, then retries confirmed', async () => {
    mockApi.getRequestsBoard.mockResolvedValue(
      board({ requests: [req({ your_vote: 1 })], voted_today: true, your_vote_request_id: 'r1' }),
    )
    mockApi.voteSongRequest
      .mockRejectedValueOnce(new ApiError('x', 409, { detail: { code: 'would_delete_request', message: 'x' } }))
      .mockResolvedValueOnce(req({ request_deleted: true }))
    render(<RequestsBoardClient />)

    fireEvent.click(await screen.findByLabelText('upvote'))  // undo own vote
    const dialog = await screen.findByRole('dialog')
    expect(dialog).toHaveTextContent(/delete this request from the board/i)

    fireEvent.click(screen.getByRole('button', { name: /delete request/i }))
    await waitFor(() => expect(mockApi.voteSongRequest).toHaveBeenLastCalledWith('r1', 'up', true))
    expect(await screen.findByText(/request was deleted/i)).toBeInTheDocument()
  })

  it('blocks submitting once today\'s vote is spent', async () => {
    mockApi.getRequestsBoard.mockResolvedValue(board({ voted_today: true, your_vote_request_id: 'r1' }))
    render(<RequestsBoardClient />)
    expect(await screen.findByTestId('vote-spent-note')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('Artist')).toBeDisabled()
    expect(screen.getByRole('button', { name: /submit request/i })).toBeDisabled()
  })

  it('confirms before a submission (it uses the vote), then shows the thank-you', async () => {
    mockApi.getRequestsBoard.mockResolvedValue(board({ requests: [] }))
    mockApi.submitSongRequest.mockResolvedValue({
      status: 'created', request: req(), canonical_artist: 'A', canonical_title: 'B', was_corrected: false,
    })
    render(<RequestsBoardClient />)
    fireEvent.change(await screen.findByPlaceholderText('Artist'), { target: { value: 'A' } })
    fireEvent.change(screen.getByPlaceholderText('Song title'), { target: { value: 'B' } })
    fireEvent.click(screen.getByRole('button', { name: /submit request/i }))

    expect(screen.getByRole('dialog')).toHaveTextContent(/uses your one vote/i)
    expect(mockApi.submitSongRequest).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: /yes, do it/i }))
    await waitFor(() => expect(mockApi.submitSongRequest).toHaveBeenCalledWith('A', 'B'))
    expect(await screen.findByTestId('thanks-panel')).toHaveTextContent(/thanks for your request/i)
  })
})
