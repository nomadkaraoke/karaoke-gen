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
const getTenant = jest.fn()
const updateTenant = jest.fn()
const getThemeTemplate = jest.fn()

jest.mock('@/lib/api', () => ({
  adminApi: {
    listTenants: (...args: unknown[]) => listTenants(...args),
    createTenant: (...args: unknown[]) => createTenant(...args),
    getTenant: (...args: unknown[]) => getTenant(...args),
    updateTenant: (...args: unknown[]) => updateTenant(...args),
    getThemeTemplate: (...args: unknown[]) => getThemeTemplate(...args),
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
  getTenant.mockResolvedValue({
    tenant: {
      id: 'vocalstar',
      name: 'Vocal Star',
      subdomain: 'vocalstar.nomadkaraoke.com',
      is_active: true,
      branding: { tagline: 'Be a Vocal Star' },
      defaults: { dropbox_path: '/Karaoke/Tracks-VocalStar', brand_prefix: 'VSTAR', distribution_mode: 'download_only' },
      auth: { allowed_email_domains: ['vocal-star.com'] },
    },
    theme_id: 'vocalstar',
    style_params: { intro: { title_color: '#ffff00' }, karaoke: {}, end: {}, cdg: {} },
    assets: ['karaoke_background.jpg', 'Oswald-SemiBold.ttf'],
    preview_url: 'https://gen.nomadkaraoke.com/en/app?preview_tenant=vocalstar',
  })
  updateTenant.mockResolvedValue({ tenant: { id: 'vocalstar', name: 'Vocal Star', subdomain: 'vocalstar.nomadkaraoke.com', is_active: true } })
  getThemeTemplate.mockResolvedValue({ style_params: { intro: {}, karaoke: {}, end: {}, cdg: {} } })
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

  it('manage loads the full theme JSON and saves config + style_params', async () => {
    render(<AdminTenantsPage />)
    await screen.findByText('Vocal Star')

    fireEvent.click(screen.getByRole('button', { name: /manage/i }))

    await waitFor(() => expect(getTenant).toHaveBeenCalledWith('vocalstar'))

    // Theme JSON prefilled into the editor
    const editor = await screen.findByPlaceholderText(/"intro":/)
    expect((editor as HTMLTextAreaElement).value).toContain('#ffff00')
    // Existing assets listed
    expect(screen.getByText('karaoke_background.jpg')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /save changes/i }))

    await waitFor(() => expect(updateTenant).toHaveBeenCalledTimes(1))
    const [tid, fd] = updateTenant.mock.calls[0] as [string, FormData]
    expect(tid).toBe('vocalstar')
    const config = JSON.parse(fd.get('config') as string)
    expect(config.name).toBe('Vocal Star')
    expect(config.defaults.dropbox_path).toBe('/Karaoke/Tracks-VocalStar')
    expect(JSON.parse(fd.get('style_params') as string).intro.title_color).toBe('#ffff00')
  })

  it('blocks save when the theme JSON is invalid', async () => {
    render(<AdminTenantsPage />)
    await screen.findByText('Vocal Star')
    fireEvent.click(screen.getByRole('button', { name: /manage/i }))
    const editor = await screen.findByPlaceholderText(/"intro":/)
    fireEvent.change(editor, { target: { value: '{ not valid json' } })
    expect(screen.getByRole('button', { name: /save changes/i })).toBeDisabled()
  })
})
