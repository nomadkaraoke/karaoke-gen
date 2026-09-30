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
const deleteTenant = jest.fn()
const provisionTenantDomain = jest.fn()

jest.mock('@/lib/api', () => ({
  adminApi: {
    listTenants: (...args: unknown[]) => listTenants(...args),
    createTenant: (...args: unknown[]) => createTenant(...args),
    getTenant: (...args: unknown[]) => getTenant(...args),
    updateTenant: (...args: unknown[]) => updateTenant(...args),
    getThemeTemplate: (...args: unknown[]) => getThemeTemplate(...args),
    deleteTenant: (...args: unknown[]) => deleteTenant(...args),
    provisionTenantDomain: (...args: unknown[]) => provisionTenantDomain(...args),
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
    domain: { hostname: 'randy-vild.nomadkaraoke.com', state: 'active', dns_ok: true, pages_status: 'active' },
  })
  getTenant.mockResolvedValue({
    tenant: {
      id: 'vocalstar',
      name: 'Vocal Star',
      subdomain: 'vocalstar.nomadkaraoke.com',
      is_active: true,
      branding: { tagline: 'Be a Vocal Star' },
      defaults: { dropbox_path: '/Karaoke/Tracks-VocalStar', brand_prefix: 'VSTAR', distribution_mode: 'download_only' },
      auth: { allowed_email_domains: ['vocal-star.com'], allowed_emails: ['boss@gmail.com'] },
    },
    theme_id: 'vocalstar',
    style_params: { intro: { title_color: '#ffff00' }, karaoke: {}, end: {}, cdg: {} },
    assets: ['karaoke_background.jpg', 'Oswald-SemiBold.ttf'],
    preview_url: 'https://gen.nomadkaraoke.com/en/app?preview_tenant=vocalstar',
    domain: { hostname: 'vocalstar.nomadkaraoke.com', state: 'missing', dns_ok: false, pages_status: null },
  })
  deleteTenant.mockResolvedValue(undefined)
  provisionTenantDomain.mockResolvedValue({
    domain: { hostname: 'vocalstar.nomadkaraoke.com', state: 'provisioning', dns_ok: true, pages_status: 'initializing' },
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
    // Dropbox delivery pre-filled from the name (same parent as the other tenants)
    expect(fd.get('dropbox_path')).toBe('/MediaUnsynced/Karaoke/Tracks-RandyVild')
    expect(fd.get('brand_prefix')).toBe('RVILD')
    // Subdomain is derived server-side, never sent
    expect(fd.get('subdomain')).toBeNull()

    // Success view shows the preview link
    expect(
      await screen.findByDisplayValue('https://gen.nomadkaraoke.com/en/app?preview_tenant=randy-vild')
    ).toBeInTheDocument()
    // List refreshed (initial + post-create)
    await waitFor(() => expect(listTenants).toHaveBeenCalledTimes(2))
  })

  it('blocks create when only one of Dropbox path / brand prefix is set', async () => {
    render(<AdminTenantsPage />)
    await screen.findByText('Vocal Star')
    fireEvent.click(screen.getByRole('button', { name: /create tenant/i }))
    fireEvent.change(await screen.findByPlaceholderText('Randy Vild'), { target: { value: 'Randy Vild' } })
    fireEvent.change(screen.getByLabelText('Brand prefix'), { target: { value: '' } })

    expect(screen.getByText(/Set both a Dropbox path and a brand prefix/)).toBeInTheDocument()
    const submitButtons = screen.getAllByRole('button', { name: /^create tenant$/i })
    fireEvent.click(submitButtons[submitButtons.length - 1])
    await new Promise((r) => setTimeout(r, 0))
    expect(createTenant).not.toHaveBeenCalled()
  })

  it('warns that download-only tenants get no Dropbox link', async () => {
    render(<AdminTenantsPage />)
    await screen.findByText('Vocal Star')
    fireEvent.click(screen.getByRole('button', { name: /create tenant/i }))
    fireEvent.change(await screen.findByPlaceholderText('Randy Vild'), { target: { value: 'Randy Vild' } })
    fireEvent.change(screen.getByLabelText('Dropbox output folder'), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText('Brand prefix'), { target: { value: '' } })
    expect(screen.getByText(/completion emails have no folder link/)).toBeInTheDocument()
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
    expect(config.auth).toEqual({ allowed_email_domains: ['vocal-star.com'], allowed_emails: ['boss@gmail.com'] })
    expect(config.subdomain).toBeUndefined()
  })

  it('edits individual allowed emails', async () => {
    render(<AdminTenantsPage />)
    await screen.findByText('Vocal Star')
    fireEvent.click(screen.getByRole('button', { name: /manage/i }))
    const emails = await screen.findByLabelText(/allowed emails/i)
    expect(emails).toHaveValue('boss@gmail.com')
    fireEvent.change(emails, { target: { value: 'Randy@Gmail.com, andrew@example.com' } })
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }))
    await waitFor(() => expect(updateTenant).toHaveBeenCalledTimes(1))
    const config = JSON.parse((updateTenant.mock.calls[0][1] as FormData).get('config') as string)
    expect(config.auth.allowed_emails).toEqual(['randy@gmail.com', 'andrew@example.com'])
  })

  it('warns when no client can sign in yet', async () => {
    render(<AdminTenantsPage />)
    await screen.findByText('Vocal Star')
    fireEvent.click(screen.getByRole('button', { name: /manage/i }))
    fireEvent.change(await screen.findByLabelText(/allowed emails/i), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText(/allowed email domains/i), { target: { value: '' } })
    expect(screen.getByText(/only nomad karaoke admins can sign in/i)).toBeInTheDocument()
  })

  it('shows domain status and can set up a missing domain', async () => {
    render(<AdminTenantsPage />)
    await screen.findByText('Vocal Star')
    fireEvent.click(screen.getByRole('button', { name: /manage/i }))
    expect(await screen.findByText('not set up')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /set up domain/i }))
    await waitFor(() => expect(provisionTenantDomain).toHaveBeenCalledWith('vocalstar'))
    expect(await screen.findByText(/provisioning \(initializing\)/)).toBeInTheDocument()
  })

  it('deletes a tenant only after typing its id', async () => {
    render(<AdminTenantsPage />)
    await screen.findByText('Vocal Star')
    fireEvent.click(screen.getByRole('button', { name: /manage/i }))
    await screen.findByLabelText(/allowed emails/i)
    fireEvent.click(screen.getByRole('button', { name: /delete tenant/i }))

    const confirmBtn = () => screen.getAllByRole('button', { name: /^delete tenant$/i }).slice(-1)[0]
    const input = await screen.findByLabelText(/to confirm/i)
    expect(confirmBtn()).toBeDisabled()
    fireEvent.change(input, { target: { value: 'vocal' } })
    expect(confirmBtn()).toBeDisabled()
    fireEvent.change(input, { target: { value: 'vocalstar' } })
    fireEvent.click(confirmBtn())

    await waitFor(() => expect(deleteTenant).toHaveBeenCalledWith('vocalstar'))
    await waitFor(() => expect(listTenants).toHaveBeenCalledTimes(2))
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


describe('Admin Tenants page — access warning edge cases', () => {
  it('treats separator-only input as no client access (matches what Save sends)', async () => {
    render(<AdminTenantsPage />)
    await screen.findByText('Vocal Star')
    fireEvent.click(screen.getByRole('button', { name: /manage/i }))
    fireEvent.change(await screen.findByLabelText(/allowed emails/i), { target: { value: ' , ' } })
    fireEvent.change(screen.getByLabelText(/allowed email domains/i), { target: { value: ',' } })
    expect(screen.getByText(/only nomad karaoke admins can sign in/i)).toBeInTheDocument()
  })
})
