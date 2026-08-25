/**
 * @jest-environment jsdom
 *
 * Tests for TenantBulkFlow — the tenant portal bulk folder-upload review table.
 *
 * Verifies the review-table interactions the plan calls out:
 * - analyze populates an editable row per proposed pair
 * - an operator can edit a cell (artist)
 * - an operator can remove a row
 * - submit runs the create → upload(x2) → complete sequence once per remaining
 *   valid row, all sharing one batch_id
 * - unpaired (mixed-only) files are surfaced as warnings, never auto-submitted
 *
 * next-intl is globally mocked in jest.setup.js (real en strings).
 */

import React from 'react'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { api } from '@/lib/api'
import { TenantBulkFlow } from '@/components/job/TenantBulkFlow'

jest.mock('@/lib/api', () => ({
  api: {
    analyzeBulk: jest.fn(),
    createJobWithUploadUrls: jest.fn(),
    uploadToSignedUrl: jest.fn(),
    completeJobUpload: jest.fn(),
  },
  ApiError: class ApiError extends Error {
    status: number
    constructor(message: string, status: number) {
      super(message)
      this.name = 'ApiError'
      this.status = status
    }
  },
}))

const mockApi = api as jest.Mocked<typeof api>

const MIXED_1 = 'S1100-1 Eddy Grant - I Dont Wanna Dance Guide.mp3'
const INST_1 = 'S1100-2 Eddy Grant - I Dont Wanna Dance BV.mp3'
const MIXED_2 = 'S1101-1 Smokey - Some Cats Know Guide.mp3'
const INST_2 = 'S1101-2 Smokey - Some Cats Know Instru.mp3'
const ORPHAN = 'S1102-2 Setzer - Straight Up Guide.mp3'

function makeFiles(): File[] {
  return [MIXED_1, INST_1, MIXED_2, INST_2, ORPHAN, 'cover.png'].map(
    name => new File(['x'], name, { type: name.endsWith('.png') ? 'image/png' : 'audio/mpeg' }),
  )
}

function analysisResponse() {
  return {
    rows: [
      { artist: 'Eddy Grant', title: 'I Dont Wanna Dance', mixed_filename: MIXED_1, instrumental_filename: INST_1, confidence: 'high', warning: null },
      { artist: 'Smokey', title: 'Some Cats Know', mixed_filename: MIXED_2, instrumental_filename: INST_2, confidence: 'high', warning: null },
    ],
    unpaired: [
      { filename: ORPHAN, reason: 'no_instrumental', artist: 'Setzer', title: 'Straight Up', role: 'mixed' },
    ],
    ignored: [{ filename: 'cover.png', reason: 'non_audio' }],
  }
}

function selectFiles() {
  const input = screen.getByTestId('bulk-files-input')
  fireEvent.change(input, { target: { files: makeFiles() } })
}

beforeEach(() => {
  jest.clearAllMocks()
  mockApi.analyzeBulk.mockResolvedValue(analysisResponse())
  mockApi.createJobWithUploadUrls.mockImplementation(async (_a, _t, _files) => ({
    status: 'success',
    job_id: `job-${Math.random().toString(36).slice(2, 8)}`,
    message: 'ok',
    upload_urls: [
      { file_type: 'audio', gcs_path: 'p1', upload_url: 'https://u/audio', content_type: 'audio/mpeg' },
      { file_type: 'existing_instrumental', gcs_path: 'p2', upload_url: 'https://u/inst', content_type: 'audio/mpeg' },
    ],
    server_version: '1',
  }))
  mockApi.uploadToSignedUrl.mockResolvedValue(undefined)
  mockApi.completeJobUpload.mockResolvedValue({ status: 'success', message: 'started' })
})

it('analyze populates one editable row per proposed pair and lists warnings', async () => {
  render(<TenantBulkFlow onJobsChanged={jest.fn()} />)
  selectFiles()

  await waitFor(() => expect(mockApi.analyzeBulk).toHaveBeenCalledTimes(1))
  // Two artist inputs (one per row).
  const artistInputs = await screen.findAllByLabelText('Artist')
  expect(artistInputs).toHaveLength(2)
  // The mixed-only orphan is surfaced as a warning (with its reason), not a row.
  expect(screen.getByText(/Some files need attention/i)).toBeInTheDocument()
  expect(screen.getByText(/no matching instrumental found/i)).toBeInTheDocument()
  expect(screen.getByText(/non-audio files ignored/i)).toBeInTheDocument()
})

it('edits a cell, removes a row, and submits the create sequence once per remaining row', async () => {
  const onJobsChanged = jest.fn()
  render(<TenantBulkFlow onJobsChanged={onJobsChanged} />)
  selectFiles()

  await screen.findAllByLabelText('Artist')

  // Edit the first row's artist cell.
  const artistInputs = screen.getAllByLabelText('Artist') as HTMLInputElement[]
  fireEvent.change(artistInputs[0], { target: { value: 'Eddy Grant Edited' } })
  expect(artistInputs[0].value).toBe('Eddy Grant Edited')

  // Remove the second row.
  const removeButtons = screen.getAllByLabelText('Remove track')
  expect(removeButtons).toHaveLength(2)
  fireEvent.click(removeButtons[1])
  await waitFor(() => expect(screen.getAllByLabelText('Artist')).toHaveLength(1))

  // Submit — one row remains.
  const submitBtn = screen.getByRole('button', { name: /Submit 1 tracks/i })
  fireEvent.click(submitBtn)

  await waitFor(() => expect(mockApi.completeJobUpload).toHaveBeenCalledTimes(1))

  // Exactly one job created, with the edited artist + tenant flags + a batch_id.
  expect(mockApi.createJobWithUploadUrls).toHaveBeenCalledTimes(1)
  const [artist, title, files, options] = mockApi.createJobWithUploadUrls.mock.calls[0]
  expect(artist).toBe('Eddy Grant Edited')
  expect(title).toBe('I Dont Wanna Dance')
  expect(options).toEqual(expect.objectContaining({ is_private: true, existing_instrumental: true }))
  expect(typeof (options as any).batch_id).toBe('string')
  expect((files as any[]).map(f => f.file_type)).toEqual(['audio', 'existing_instrumental'])

  // Two uploads (mixed + instrumental) for the single row.
  expect(mockApi.uploadToSignedUrl).toHaveBeenCalledTimes(2)
  expect(onJobsChanged).toHaveBeenCalled()
})

it('submits every valid row sharing a single batch_id', async () => {
  render(<TenantBulkFlow onJobsChanged={jest.fn()} />)
  selectFiles()
  await screen.findAllByLabelText('Artist')

  fireEvent.click(screen.getByRole('button', { name: /Submit 2 tracks/i }))
  await waitFor(() => expect(mockApi.completeJobUpload).toHaveBeenCalledTimes(2))

  expect(mockApi.createJobWithUploadUrls).toHaveBeenCalledTimes(2)
  const batchIds = mockApi.createJobWithUploadUrls.mock.calls.map(c => (c[3] as any).batch_id)
  expect(new Set(batchIds).size).toBe(1)
})

it('exports TenantBulkFlow as a named export', () => {
  expect(typeof TenantBulkFlow).toBe('function')
})
