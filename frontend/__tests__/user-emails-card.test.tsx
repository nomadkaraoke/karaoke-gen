/**
 * @jest-environment jsdom
 *
 * Tests for UserEmailsCard — the admin "Emails sent to this user" section and
 * its inbox-fidelity preview modal.
 */

import React from 'react'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'
import { UserEmailsCard } from '@/components/admin/user-emails-card'

jest.mock('@/lib/api', () => ({
  adminApi: {
    getUserEmails: jest.fn(),
    getEmailDetail: jest.fn(),
  },
}))

import { adminApi } from '@/lib/api'

const getUserEmails = adminApi.getUserEmails as jest.Mock
const getEmailDetail = adminApi.getEmailDetail as jest.Mock

describe('UserEmailsCard', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('lists emails returned for the user', async () => {
    getUserEmails.mockResolvedValue({
      email: 'u@x.com', count: 2, postmark_available: true,
      emails: [
        { message_id: 'm1', source: 'postmark', subject: 'Welcome to Nomad', sent_at: '2026-08-20T10:00:00Z', status: 'Delivered', email_type: 'welcome' },
        { message_id: 'm2', source: 'log', subject: 'Your receipt', sent_at: '2026-08-19T10:00:00Z', email_type: 'credits_added' },
      ],
    })
    render(<UserEmailsCard email="u@x.com" />)
    await waitFor(() => expect(screen.getByText('Welcome to Nomad')).toBeInTheDocument())
    expect(screen.getByText('Your receipt')).toBeInTheDocument()
    expect(getUserEmails).toHaveBeenCalledWith('u@x.com')
  })

  it('shows an empty state when there are no emails', async () => {
    getUserEmails.mockResolvedValue({ email: 'u@x.com', count: 0, postmark_available: true, emails: [] })
    render(<UserEmailsCard email="u@x.com" />)
    await waitFor(() => expect(screen.getByText(/No emails found/i)).toBeInTheDocument())
  })

  it('opens the preview modal and renders the email HTML in an iframe', async () => {
    getUserEmails.mockResolvedValue({
      email: 'u@x.com', count: 1, postmark_available: true,
      emails: [{ message_id: 'm1', source: 'postmark', subject: 'Welcome', sent_at: '2026-08-20T10:00:00Z', status: 'Delivered' }],
    })
    getEmailDetail.mockResolvedValue({
      message_id: 'm1', source: 'postmark', subject: 'Welcome', from_email: 'gen@x.com',
      to: 'u@x.com', sent_at: '2026-08-20T10:00:00Z', status: 'Delivered',
      html_body: '<h1>Hello there</h1>', open_count: 2,
    })

    render(<UserEmailsCard email="u@x.com" />)
    await waitFor(() => expect(screen.getByText('Welcome')).toBeInTheDocument())

    fireEvent.click(screen.getByText('Welcome'))

    await waitFor(() => expect(getEmailDetail).toHaveBeenCalledWith('m1', 'postmark'))
    // Metadata + iframe with the exact HTML the user received.
    await waitFor(() => {
      const iframe = document.querySelector('iframe[title="Email preview"]') as HTMLIFrameElement
      expect(iframe).toBeTruthy()
      expect(iframe.getAttribute('srcDoc') ?? iframe.getAttribute('srcdoc')).toContain('Hello there')
    })
  })

  it('shows an error when loading fails', async () => {
    getUserEmails.mockRejectedValue(new Error('nope'))
    render(<UserEmailsCard email="u@x.com" />)
    await waitFor(() => expect(screen.getByText('nope')).toBeInTheDocument())
  })
})
