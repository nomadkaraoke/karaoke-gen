/**
 * @jest-environment jsdom
 *
 * Tests for the admin Tenants page — listing tenants and creating one.
 * adminApi is mocked.
 */

import React from 'react'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'

const listTenants = jest.fn()
const createTenant = jest.fn()

jest.mock('@/lib/api', () => ({
  adminApi: {
    listTenants: (...args: unknown[]) => listTenants(...args),
    createTenant: (...args: unknown[]) => createTenant(...args),
  },
}))

import AdminTenantsPage from '@/app/admin/tenants/page'

beforeEach(() => {
  jest.clearAllMocks()
  listTenants.mockResolvedValue({
    tenants: [
      {
        id: 'vocalstar',
        name: 'Vocal Star',
        subdomain: 'vocalstar.nomadkaraoke.com',
        is_active: true,
        dropbox_path: '/Karaoke/Tracks-VocalStar',
      },
    ],
  })
  createTenant.mockResolvedValue({
    tenant: { id: 'randy-vild', name: 'Randy Vild', subdomain: 'randy-vild.nomadkaraoke.com', is_active: true },
    preview_url: 'https://gen.nomadkaraoke.com/en/app?preview_tenant=randy-vild',
    subdomain_url: 'https://randy-vild.nomadkaraoke.com',
  })
})

describe('Admin Tenants page', () => {
  it('lists existing tenants', async () => {
    render(<AdminTenantsPage />)
    expect(await screen.findByText('Vocal Star')).toBeInTheDocument()
    expect(screen.getByText('vocalstar')).toBeInTheDocument()
    expect(screen.getByText('vocalstar.nomadkaraoke.com')).toBeInTheDocument()
  })

  it('auto-derives the tenant id from the name and creates a tenant', async () => {
    render(<AdminTenantsPage />)
    await screen.findByText('Vocal Star')

    fireEvent.click(screen.getByRole('button', { name: /create tenant/i }))

    const nameInput = await screen.findByPlaceholderText('Randy Vild')
    fireEvent.change(nameInput, { target: { value: 'Randy Vild' } })

    // id auto-derived
    expect(screen.getByPlaceholderText('randy-vild')).toHaveValue('randy-vild')

    // Submit (the dialog's own "Create tenant" button)
    const submitButtons = screen.getAllByRole('button', { name: /^create tenant$/i })
    fireEvent.click(submitButtons[submitButtons.length - 1])

    await waitFor(() => expect(createTenant).toHaveBeenCalledTimes(1))
    const fd = createTenant.mock.calls[0][0] as FormData
    expect(fd.get('name')).toBe('Randy Vild')
    expect(fd.get('tenant_id')).toBe('randy-vild')

    // Success view shows the preview link
    expect(
      await screen.findByDisplayValue('https://gen.nomadkaraoke.com/en/app?preview_tenant=randy-vild')
    ).toBeInTheDocument()
    // List refreshed (initial + post-create)
    await waitFor(() => expect(listTenants).toHaveBeenCalledTimes(2))
  })
})
