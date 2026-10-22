/**
 * @jest-environment jsdom
 *
 * Tests for BulkAlbumMode — the Artist → Album → Tracks picker.
 *
 * Verifies the guidance cues:
 * - the step strip marks the current step and lets completed steps jump back
 * - leaving the tracklist clears its rows (a stale album can't be submitted)
 * - select all / none toggles every track
 * - BulkMode hides settings + submit in album mode until a tracklist is loaded
 *
 * next-intl is globally mocked in jest.setup.js (real en strings).
 */

import React, { useState } from 'react'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { api } from '@/lib/api'
import { BulkAlbumMode } from '@/components/job/bulk/BulkAlbumMode'
import { BulkMode } from '@/components/job/bulk/BulkMode'
import type { BulkSongRow } from '@/components/job/bulk/types'

jest.mock('@/lib/api', () => ({
  api: {
    searchAlbumArtists: jest.fn(),
    getAlbums: jest.fn(),
    getAlbumTracklist: jest.fn(),
    bulkSubmit: jest.fn(),
  },
}))

jest.mock('@/lib/auth', () => ({
  useAuth: () => ({ user: { email: 'a@example.com', credits: 10 } }),
}))

const mockApi = api as jest.Mocked<typeof api>

const ARTIST = { mbid: 'artist-1', name: 'Britney Spears', disambiguation: 'US pop singer' }
const ALBUM = {
  release_group_mbid: 'rg-1', title: 'Oops!...I Did It Again', secondary_types: [],
  first_release_date: '2000-05-16', is_studio: true,
}
const TRACKLIST = {
  title: 'Oops!...I Did It Again',
  tracks: [
    { title: 'Oops!...I Did It Again', position: 1, available: false, is_extra: false, brands: [] },
    { title: 'Stronger', position: 2, available: false, is_extra: false, brands: [] },
  ],
  variants: [],
}

let latestRows: BulkSongRow[] = []

function Harness() {
  const [rows, setRows] = useState<BulkSongRow[]>([])
  latestRows = rows
  return <BulkAlbumMode rows={rows} onChange={setRows} />
}

async function walkToTracklist() {
  fireEvent.change(screen.getByPlaceholderText('Search for an artist…'), { target: { value: 'Britney' } })
  fireEvent.click(screen.getByRole('button', { name: /^Search$/ }))
  fireEvent.click(await screen.findByRole('button', { name: /Britney Spears/ }))
  fireEvent.click(await screen.findByRole('button', { name: /Oops!\.\.\.I Did It Again/ }))
  await screen.findByText('Choose tracks')
}

beforeEach(() => {
  jest.clearAllMocks()
  mockApi.searchAlbumArtists.mockResolvedValue([ARTIST] as any)
  mockApi.getAlbums.mockResolvedValue([ALBUM] as any)
  mockApi.getAlbumTracklist.mockResolvedValue(TRACKLIST as any)
})

describe('BulkAlbumMode', () => {
  it('starts on the artist step with the step strip', () => {
    render(<Harness />)
    expect(screen.getByText('Choose an artist')).toBeInTheDocument()
    expect(screen.getByText('1. Artist').closest('[aria-current="step"]')).not.toBeNull()
    expect(screen.getByText('2. Album').closest('[aria-current="step"]')).toBeNull()
  })

  it('walks artist → album → tracks and shows picks in the step strip', async () => {
    render(<Harness />)
    fireEvent.change(screen.getByPlaceholderText('Search for an artist…'), { target: { value: 'Britney' } })
    fireEvent.click(screen.getByRole('button', { name: /^Search$/ }))
    fireEvent.click(await screen.findByRole('button', { name: /Britney Spears/ }))

    expect(await screen.findByText('Choose an album')).toBeInTheDocument()
    expect(screen.getByText('Albums by Britney Spears')).toBeInTheDocument()
    expect(screen.getByText('2. Album').closest('[aria-current="step"]')).not.toBeNull()

    fireEvent.click(await screen.findByRole('button', { name: /Oops!\.\.\.I Did It Again/ }))
    expect(await screen.findByText('Choose tracks')).toBeInTheDocument()
    expect(screen.getByText('2 of 2 tracks selected')).toBeInTheDocument()
    expect(latestRows).toHaveLength(2)
  })

  it('select none / select all toggles every track', async () => {
    render(<Harness />)
    await walkToTracklist()
    fireEvent.click(screen.getByRole('button', { name: 'Select none' }))
    expect(screen.getByText('0 of 2 tracks selected')).toBeInTheDocument()
    expect(latestRows.every((r) => !r.selected)).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Select all' }))
    expect(latestRows.every((r) => r.selected)).toBe(true)
  })

  it('jumping back to the album step clears the tracklist rows', async () => {
    render(<Harness />)
    await walkToTracklist()
    expect(latestRows).toHaveLength(2)
    fireEvent.click(screen.getByRole('button', { name: /2\. Album/ }))
    expect(await screen.findByText('Choose an album')).toBeInTheDocument()
    expect(latestRows).toHaveLength(0)
  })

  it('a tracklist that resolves after navigating back is dropped', async () => {
    let resolveTracklist: (v: unknown) => void = () => {}
    mockApi.getAlbumTracklist.mockReturnValue(new Promise((r) => { resolveTracklist = r }) as any)
    render(<Harness />)
    fireEvent.change(screen.getByPlaceholderText('Search for an artist…'), { target: { value: 'Britney' } })
    fireEvent.click(screen.getByRole('button', { name: /^Search$/ }))
    fireEvent.click(await screen.findByRole('button', { name: /Britney Spears/ }))
    fireEvent.click(await screen.findByRole('button', { name: /Oops!\.\.\.I Did It Again/ }))

    fireEvent.click(screen.getByRole('button', { name: /1\. Artist/ }))
    await act(async () => { resolveTracklist(TRACKLIST) })

    expect(screen.getByText('Choose an artist')).toBeInTheDocument()
    expect(screen.queryByText('Choose tracks')).toBeNull()
    expect(latestRows).toHaveLength(0)
  })

  it('jumping back to the artist step returns to search', async () => {
    render(<Harness />)
    await walkToTracklist()
    fireEvent.click(screen.getByRole('button', { name: /1\. Artist/ }))
    expect(await screen.findByText('Choose an artist')).toBeInTheDocument()
    expect(latestRows).toHaveLength(0)
  })
})

describe('BulkMode (album sub-mode)', () => {
  it('hides batch settings and submit until a tracklist is loaded', async () => {
    render(<BulkMode />)
    expect(screen.queryByText('Batch settings')).toBeNull()
    expect(screen.queryByRole('button', { name: /Create \d+ karaoke videos/ })).toBeNull()

    await walkToTracklist()
    await waitFor(() => expect(screen.getByText('Batch settings')).toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Create 2 karaoke videos' })).toBeInTheDocument()
  })

  it('submits the album and each track position so the backend searches the album once', async () => {
    mockApi.bulkSubmit.mockResolvedValue({ batch_id: 'b1', job_ids: ['j1', 'j2'], total: 2 } as any)
    render(<BulkMode />)
    await walkToTracklist()
    fireEvent.click(await screen.findByRole('button', { name: 'Create 2 karaoke videos' }))
    await waitFor(() => expect(mockApi.bulkSubmit).toHaveBeenCalled())

    const payload = mockApi.bulkSubmit.mock.calls[0][0]
    expect(payload.album).toEqual({
      artist: 'Britney Spears', title: 'Oops!...I Did It Again', release_group_mbid: 'rg-1', release_mbid: null,
    })
    expect(payload.songs.map((s: any) => [s.title, s.position])).toEqual([
      ['Oops!...I Did It Again', 1], ['Stronger', 2],
    ])
  })
})

describe('BulkAlbumMode cover art', () => {
  async function walkToAlbums() {
    fireEvent.change(screen.getByPlaceholderText('Search for an artist…'), { target: { value: 'Britney' } })
    fireEvent.click(screen.getByRole('button', { name: /^Search$/ }))
    fireEvent.click(await screen.findByRole('button', { name: /Britney Spears/ }))
    await screen.findByText('Choose an album')
  }

  const coverImg = (container: HTMLElement) =>
    container.querySelector('img[src^="https://coverartarchive.org/"]') as HTMLImageElement | null

  it('requests the release-group front cover for each album row, hidden until loaded', async () => {
    const { container } = render(<Harness />)
    await walkToAlbums()
    const img = coverImg(container)!
    expect(img.getAttribute('src')).toBe('https://coverartarchive.org/release-group/rg-1/front-250')
    expect(img).toHaveAttribute('loading', 'lazy')
    expect(img.className).toContain('invisible')
    fireEvent.load(img)
    expect(img.className).not.toContain('invisible')
  })

  it('falls back to the disc icon when the album has no cover art', async () => {
    const { container } = render(<Harness />)
    await walkToAlbums()
    fireEvent.error(coverImg(container)!)
    expect(coverImg(container)).toBeNull()
    // The album row is still there and clickable.
    expect(screen.getByRole('button', { name: /Oops!\.\.\.I Did It Again/ })).toBeEnabled()
  })

  it('shows the picked album cover on the tracklist step', async () => {
    const { container } = render(<Harness />)
    await walkToTracklist()
    expect(coverImg(container)?.getAttribute('src')).toBe(
      'https://coverartarchive.org/release-group/rg-1/front-250',
    )
  })
})

describe('BulkAlbumMode artist search results', () => {
  async function search(q: string) {
    fireEvent.change(screen.getByPlaceholderText('Search for an artist…'), { target: { value: q } })
    fireEvent.click(screen.getByRole('button', { name: /^Search$/ }))
  }

  it('shows a no-results message (not a blank list) when nothing matches', async () => {
    mockApi.searchAlbumArtists.mockResolvedValue([] as any)
    render(<Harness />)
    await search('Zzyzx Nobody')
    const empty = await screen.findByTestId('bulk-artist-no-results')
    expect(empty).toHaveTextContent('No artists match “Zzyzx Nobody”.')
    // No switch button without a handler.
    expect(screen.queryByRole('button', { name: 'Add songs by name' })).toBeNull()
  })

  it('hides the no-results message while a new search is loading and when results arrive', async () => {
    mockApi.searchAlbumArtists.mockResolvedValueOnce([] as any)
    render(<Harness />)
    await search('Nobody')
    await screen.findByTestId('bulk-artist-no-results')
    let resolve: (v: any) => void = () => {}
    mockApi.searchAlbumArtists.mockReturnValueOnce(new Promise((r) => { resolve = r }) as any)
    await search('Britney')
    expect(screen.queryByTestId('bulk-artist-no-results')).toBeNull()
    await act(async () => resolve([ARTIST]))
    expect(await screen.findByRole('button', { name: /Britney Spears/ })).toBeInTheDocument()
    expect(screen.queryByTestId('bulk-artist-no-results')).toBeNull()
  })

  it('does not show the no-results message when the lookup fails', async () => {
    mockApi.searchAlbumArtists.mockRejectedValue(new Error('502'))
    render(<Harness />)
    await search('Britney')
    expect(await screen.findByText('Lookup failed. Please try again.')).toBeInTheDocument()
    expect(screen.queryByTestId('bulk-artist-no-results')).toBeNull()
  })

  it('explains alias matches in the row subtitle', async () => {
    mockApi.searchAlbumArtists.mockResolvedValue([
      { mbid: 'a-2', name: '宇多田ヒカル', disambiguation: 'Japanese-American singer-songwriter', alias: 'Hikaru Utada' },
    ] as any)
    render(<Harness />)
    await search('hikaru utada')
    expect(await screen.findByText('Also known as Hikaru Utada · Japanese-American singer-songwriter')).toBeInTheDocument()
  })

  it('in BulkMode, the no-results state offers a switch to By text', async () => {
    mockApi.searchAlbumArtists.mockResolvedValue([] as any)
    localStorage.clear() // an earlier submit test remembers its batch, which opens batch progress
    render(<BulkMode />)
    await search('Zzyzx Nobody')
    fireEvent.click(await screen.findByRole('button', { name: 'Add songs by name' }))
    await waitFor(() => expect(screen.getByRole('tab', { name: /By text/ })).toHaveAttribute('data-state', 'active'))
  })
})
