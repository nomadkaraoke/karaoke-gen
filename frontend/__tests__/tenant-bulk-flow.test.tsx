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

// The resumable engine is unit-tested separately; here we assert the component
// routes resumable entries through it (and legacy entries through signed PUT).
jest.mock('@/lib/resumable-upload', () => ({
  uploadResumable: jest.fn().mockResolvedValue(undefined),
  ResumableUploadError: class ResumableUploadError extends Error {
    status: number
    permanent: boolean
    constructor(message: string, status: number, permanent: boolean) {
      super(message)
      this.name = 'ResumableUploadError'
      this.status = status
      this.permanent = permanent
    }
  },
}))

// IndexedDB isn't available in jsdom; mock persistence but keep the real
// matchRepickedFile so the recovery flow exercises genuine matching logic.
jest.mock('@/lib/upload-recovery', () => {
  const actual = jest.requireActual('@/lib/upload-recovery')
  return {
    ...actual,
    saveRowSessions: jest.fn().mockResolvedValue(undefined),
    markRowDone: jest.fn().mockResolvedValue(undefined),
    clearBatch: jest.fn().mockResolvedValue(undefined),
    loadPendingBatch: jest.fn().mockResolvedValue(null),
  }
})

import { uploadResumable } from '@/lib/resumable-upload'
import { saveRowSessions, markRowDone, loadPendingBatch } from '@/lib/upload-recovery'

const mockApi = api as jest.Mocked<typeof api>
const mockUploadResumable = uploadResumable as jest.MockedFunction<typeof uploadResumable>
const mockLoadPendingBatch = loadPendingBatch as jest.MockedFunction<typeof loadPendingBatch>

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
  mockLoadPendingBatch.mockResolvedValue(null)
  mockUploadResumable.mockResolvedValue(undefined)
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

it('retries a failed row by resuming its job, never creating a duplicate', async () => {
  // First submit: creation succeeds but the upload fails → row goes to error,
  // keeping its jobId + signed URLs.
  mockApi.uploadToSignedUrl.mockRejectedValueOnce(new Error('network blip'))

  // Use a single-row analysis for a precise assertion.
  mockApi.analyzeBulk.mockResolvedValue({
    rows: [
      { artist: 'Eddy Grant', title: 'I Dont Wanna Dance', mixed_filename: MIXED_1, instrumental_filename: INST_1, confidence: 'high', warning: null },
    ],
    unpaired: [],
    ignored: [],
  })

  render(<TenantBulkFlow onJobsChanged={jest.fn()} />)
  selectFiles()
  await screen.findAllByLabelText('Artist')

  fireEvent.click(screen.getByRole('button', { name: /Submit 1 tracks/i }))
  await waitFor(() => expect(mockApi.createJobWithUploadUrls).toHaveBeenCalledTimes(1))
  await waitFor(() => expect(screen.getByText(/will retry on submit/i)).toBeInTheDocument())

  // Retry: the row is still submittable and resumes the SAME job (no 2nd create).
  const retryBtn = screen.getByRole('button', { name: /Submit 1 tracks/i })
  expect(retryBtn).not.toBeDisabled()
  fireEvent.click(retryBtn)
  await waitFor(() => expect(mockApi.completeJobUpload).toHaveBeenCalledTimes(1))
  expect(mockApi.createJobWithUploadUrls).toHaveBeenCalledTimes(1) // never duplicated
})

it('surfaces a caution for low-confidence / warned rows', async () => {
  mockApi.analyzeBulk.mockResolvedValue({
    rows: [
      { artist: 'Eddy Grant', title: 'I Dont Wanna Dance', mixed_filename: MIXED_1, instrumental_filename: INST_1, confidence: 'low', warning: null },
      { artist: 'Smokey', title: 'Some Cats Know', mixed_filename: MIXED_2, instrumental_filename: INST_2, confidence: 'high', warning: 'labels were ambiguous' },
    ],
    unpaired: [],
    ignored: [],
  })

  render(<TenantBulkFlow onJobsChanged={jest.fn()} />)
  selectFiles()
  await screen.findAllByLabelText('Artist')

  // Low-confidence row shows the generic double-check caution.
  expect(screen.getByText(/Low-confidence match/i)).toBeInTheDocument()
  // Explicit analyzer warning is shown verbatim.
  expect(screen.getByText(/labels were ambiguous/i)).toBeInTheDocument()
})

it('requests resumable mode and uploads via the resumable engine', async () => {
  mockApi.analyzeBulk.mockResolvedValue({
    rows: [
      { artist: 'Eddy Grant', title: 'I Dont Wanna Dance', mixed_filename: MIXED_1, instrumental_filename: INST_1, confidence: 'high', warning: null },
    ],
    unpaired: [],
    ignored: [],
  })
  mockApi.createJobWithUploadUrls.mockResolvedValue({
    status: 'success',
    job_id: 'job-resumable',
    message: 'ok',
    upload_urls: [
      { file_type: 'audio', gcs_path: 'p1', upload_url: 'https://session/audio', content_type: 'audio/mpeg', resumable: true },
      { file_type: 'existing_instrumental', gcs_path: 'p2', upload_url: 'https://session/inst', content_type: 'audio/mpeg', resumable: true },
    ],
    server_version: '1',
  })

  render(<TenantBulkFlow onJobsChanged={jest.fn()} />)
  selectFiles()
  await screen.findAllByLabelText('Artist')
  fireEvent.click(screen.getByRole('button', { name: /Submit 1 tracks/i }))
  await waitFor(() => expect(mockApi.completeJobUpload).toHaveBeenCalledTimes(1))

  // Backend asked for resumable session URIs.
  const options = mockApi.createJobWithUploadUrls.mock.calls[0][3] as any
  expect(options.upload_mode).toBe('resumable')
  // Both files went through the resumable engine, not the signed-PUT path.
  expect(mockUploadResumable).toHaveBeenCalledTimes(2)
  expect(mockUploadResumable.mock.calls.map(c => c[0])).toEqual(['https://session/audio', 'https://session/inst'])
  expect(mockApi.uploadToSignedUrl).not.toHaveBeenCalled()
  // Session state persisted for re-pick recovery, then cleaned up on success.
  expect(saveRowSessions).toHaveBeenCalledTimes(1)
  expect((saveRowSessions as jest.Mock).mock.calls[0][0]).toMatchObject({
    jobId: 'job-resumable',
    files: expect.arrayContaining([expect.objectContaining({ sessionUri: 'https://session/audio' })]),
  })
  expect(markRowDone).toHaveBeenCalledTimes(1)
})

it('offers to resume an unfinished batch and resumes without re-creating jobs', async () => {
  const mixedFile = new File(['x'], MIXED_1, { type: 'audio/mpeg', lastModified: 111 })
  const instFile = new File(['x'], INST_1, { type: 'audio/mpeg', lastModified: 222 })
  mockLoadPendingBatch.mockResolvedValue({
    batchId: 'batch-recovered',
    rows: [
      {
        key: 'batch-recovered:row-1',
        batchId: 'batch-recovered',
        rowId: 'row-1',
        jobId: 'job-restored',
        artist: 'Eddy Grant',
        title: 'I Dont Wanna Dance',
        createdAt: Date.now(),
        files: [
          { fileType: 'audio', identity: MIXED_1, name: MIXED_1, size: mixedFile.size, lastModified: 111, sessionUri: 'https://session/audio' },
          { fileType: 'existing_instrumental', identity: INST_1, name: INST_1, size: instFile.size, lastModified: 222, sessionUri: 'https://session/inst' },
        ],
      },
    ],
  })

  render(<TenantBulkFlow onJobsChanged={jest.fn()} />)

  // Banner appears; choose to resume, then re-pick the folder.
  await screen.findByTestId('resume-banner')
  fireEvent.click(screen.getByRole('button', { name: /Choose folder to resume/i }))
  fireEvent.change(screen.getByTestId('bulk-folder-input'), { target: { files: [mixedFile, instFile] } })

  // Review table rebuilt from the persisted batch — no fresh analyze call.
  const artistInputs = await screen.findAllByLabelText('Artist') as HTMLInputElement[]
  expect(artistInputs).toHaveLength(1)
  expect(artistInputs[0].value).toBe('Eddy Grant')
  expect(mockApi.analyzeBulk).not.toHaveBeenCalled()

  // Submitting resumes the existing job's sessions — no job creation.
  fireEvent.click(screen.getByRole('button', { name: /Submit 1 tracks/i }))
  await waitFor(() => expect(mockApi.completeJobUpload).toHaveBeenCalledWith('job-restored', ['audio', 'existing_instrumental']))
  expect(mockApi.createJobWithUploadUrls).not.toHaveBeenCalled()
  expect(mockUploadResumable.mock.calls.map(c => c[0])).toEqual(['https://session/audio', 'https://session/inst'])
})

it('matchRepickedFile requires an exact size and disambiguates by mtime', () => {
  const { matchRepickedFile } = jest.requireActual('@/lib/upload-recovery')
  const persisted = { fileType: 'audio', identity: 'a.mp3', name: 'a.mp3', size: 1, lastModified: 111, sessionUri: 's' }
  const right = new File(['x'], 'a.mp3', { lastModified: 111 })
  const wrongSize = new File(['xx'], 'a.mp3', { lastModified: 111 })
  const wrongMtime = new File(['x'], 'a.mp3', { lastModified: 999 })

  expect(matchRepickedFile(persisted, [right])).toBe(right)
  // Same name but different size = a different file — never resumed.
  expect(matchRepickedFile(persisted, [wrongSize])).toBeNull()
  // Ambiguous same-name same-size candidates → mtime decides.
  expect(matchRepickedFile(persisted, [wrongMtime, right])).toBe(right)
})

it('exports TenantBulkFlow as a named export', () => {
  expect(typeof TenantBulkFlow).toBe('function')
})
