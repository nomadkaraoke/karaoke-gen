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
    getJob: jest.fn(),
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
  ;(mockApi.getJob as jest.Mock).mockResolvedValue({ status: 'pending' })
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

it('shows a batch-wide upload modal with byte progress, track counts and connection state', async () => {
  // Two 1 MB rows (mixed + instrumental each 512 KB) so aggregate bytes are exact.
  const half = 'x'.repeat(512 * 1024)
  const bigFiles = [MIXED_1, INST_1, MIXED_2, INST_2].map(name => new File([half], name, { type: 'audio/mpeg' }))
  mockApi.createJobWithUploadUrls.mockImplementation(async () => ({
    status: 'success',
    job_id: `job-${Math.random().toString(36).slice(2, 8)}`,
    message: 'ok',
    upload_urls: [
      { file_type: 'audio', gcs_path: 'p1', upload_url: 'https://session/audio', content_type: 'audio/mpeg', resumable: true },
      { file_type: 'existing_instrumental', gcs_path: 'p2', upload_url: 'https://session/inst', content_type: 'audio/mpeg', resumable: true },
    ],
    server_version: '1',
  }))
  // Hold every upload open so the test can drive progress by hand.
  const pending: { onProgress: (p: any) => void; resolve: () => void }[] = []
  mockUploadResumable.mockImplementation((_url, _file, opts: any) =>
    new Promise<void>(resolve => { pending.push({ onProgress: opts.onProgress, resolve }) }))

  render(<TenantBulkFlow onJobsChanged={jest.fn()} />)
  fireEvent.change(screen.getByTestId('bulk-files-input'), { target: { files: bigFiles } })
  await screen.findAllByLabelText('Artist')
  fireEvent.click(screen.getByRole('button', { name: /Submit 2 tracks/i }))

  const modal = await screen.findByTestId('upload-progress-modal')
  expect(within(modal).getByText('Uploading 2 tracks')).toBeInTheDocument()
  await waitFor(() => expect(pending).toHaveLength(2)) // both rows' mixed files in flight

  // Row 1 has sent 512 KB of its 1 MB → 25% of the 2 MB batch.
  const { act } = await import('react')
  act(() => pending[0].onProgress({ loaded: 512 * 1024, bytesPerSecond: 1024, state: 'uploading' }))
  expect(within(modal).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '25')
  expect(within(modal).getByText(/0\.5 MB of 2\.0 MB/)).toBeInTheDocument()
  expect(within(modal).getByTestId('upload-progress-detail')).toHaveTextContent('0 of 2 tracks submitted')

  // The resumable engine waiting for the network surfaces as a notice.
  act(() => pending[1].onProgress({ loaded: 0, bytesPerSecond: null, state: 'waiting-online' }))
  expect(within(modal).getByText(/Waiting for connection/)).toBeInTheDocument()

  // Finish row 1 (mixed then instrumental) → 1 of 2 submitted.
  act(() => pending[0].resolve())
  await waitFor(() => expect(pending).toHaveLength(3))
  act(() => pending[2].resolve())
  await waitFor(() => expect(screen.getByTestId('upload-progress-detail')).toHaveTextContent('1 of 2 tracks submitted'))

  // Finish row 2 → modal closes and the done summary shows.
  act(() => pending[1].resolve())
  await waitFor(() => expect(pending).toHaveLength(4))
  act(() => pending[3].resolve())
  await waitFor(() => expect(screen.queryByTestId('upload-progress-modal')).not.toBeInTheDocument())
  expect(mockApi.completeJobUpload).toHaveBeenCalledTimes(2)
})

it('drops a failed row out of the modal totals and reports it', async () => {
  mockApi.uploadToSignedUrl.mockRejectedValueOnce(new Error('network blip'))
  let releaseSecond: () => void = () => {}
  mockApi.uploadToSignedUrl.mockImplementation(() => new Promise<void>(r => { releaseSecond = r }))

  render(<TenantBulkFlow onJobsChanged={jest.fn()} />)
  selectFiles()
  await screen.findAllByLabelText('Artist')
  fireEvent.click(screen.getByRole('button', { name: /Submit 2 tracks/i }))

  await waitFor(() => expect(screen.getByTestId('upload-progress-detail')).toHaveTextContent('1 failed'))
  const { act } = await import('react')
  // Let the surviving row finish; the modal closes and the failed row stays retryable.
  for (let i = 0; i < 2; i++) {
    act(() => releaseSecond())
    await new Promise(r => setTimeout(r, 0))
  }
  await waitFor(() => expect(screen.queryByTestId('upload-progress-modal')).not.toBeInTheDocument())
  expect(screen.getByText(/will retry on submit/i)).toBeInTheDocument()
})

it('a job the server rejected is retried as a fresh job, not re-finalized', async () => {
  // Regression: a duration-mismatch 400 cancels the job server-side. The row
  // used to keep the cancelled jobId, and the retry's 400 ("not pending") was
  // then mistaken for "already processing" — showing Submitted for a dead job.
  const { ApiError } = jest.requireMock('@/lib/api')
  mockApi.analyzeBulk.mockResolvedValue({
    rows: [{ artist: 'Eddy Grant', title: 'I Dont Wanna Dance', mixed_filename: MIXED_1, instrumental_filename: INST_1, confidence: 'high', warning: null }],
    unpaired: [],
    ignored: [],
  })
  mockApi.completeJobUpload
    .mockRejectedValueOnce(new ApiError('Duration mismatch: cancelled', 400))
    .mockResolvedValueOnce({ status: 'success', message: 'started' })
  ;(mockApi.getJob as jest.Mock).mockResolvedValueOnce({ status: 'cancelled' })

  render(<TenantBulkFlow onJobsChanged={jest.fn()} />)
  selectFiles()
  await screen.findAllByLabelText('Artist')
  fireEvent.click(screen.getByRole('button', { name: /Submit 1 tracks/i }))
  await waitFor(() => expect(screen.getByText(/Duration mismatch: cancelled/)).toBeInTheDocument())

  fireEvent.click(screen.getByRole('button', { name: /Submit 1 tracks/i }))
  await waitFor(() => expect(mockApi.completeJobUpload).toHaveBeenCalledTimes(2))
  expect(mockApi.createJobWithUploadUrls).toHaveBeenCalledTimes(2)
  const [first, second] = mockApi.completeJobUpload.mock.calls.map(c => c[0])
  expect(second).not.toBe(first)
  expect(markRowDone).toHaveBeenCalled()
})

it('a recovered row whose job was cancelled is not reported as submitted', async () => {
  const { ApiError } = jest.requireMock('@/lib/api')
  const mixedFile = new File(['x'], MIXED_1, { type: 'audio/mpeg', lastModified: 111 })
  const instFile = new File(['x'], INST_1, { type: 'audio/mpeg', lastModified: 222 })
  mockLoadPendingBatch.mockResolvedValue({
    batchId: 'b', rows: [{
      key: 'b:row-1', batchId: 'b', rowId: 'row-1', jobId: 'job-dead', artist: 'Eddy Grant', title: 'I Dont Wanna Dance', createdAt: Date.now(),
      files: [
        { fileType: 'audio', identity: MIXED_1, name: MIXED_1, size: 1, lastModified: 111, sessionUri: 'https://session/audio' },
        { fileType: 'existing_instrumental', identity: INST_1, name: INST_1, size: 1, lastModified: 222, sessionUri: 'https://session/inst' },
      ],
    }],
  })
  mockApi.completeJobUpload.mockRejectedValueOnce(new ApiError('Job is not pending', 400))
  ;(mockApi.getJob as jest.Mock).mockResolvedValue({ job_id: 'job-dead', status: 'cancelled' })

  render(<TenantBulkFlow onJobsChanged={jest.fn()} />)
  await screen.findByTestId('resume-banner')
  fireEvent.click(screen.getByRole('button', { name: /Choose folder to resume/i }))
  fireEvent.change(screen.getByTestId('bulk-folder-input'), { target: { files: [mixedFile, instFile] } })
  await screen.findAllByLabelText('Artist')
  fireEvent.click(screen.getByRole('button', { name: /Submit 1 tracks/i }))

  await waitFor(() => expect(screen.getByText(/Job is not pending/)).toBeInTheDocument())
  expect(mockApi.getJob).toHaveBeenCalledWith('job-dead')
  expect(screen.queryByText(/Submitted/)).not.toBeInTheDocument()
})

it('a recovered row whose job is already processing counts as submitted', async () => {
  const { ApiError } = jest.requireMock('@/lib/api')
  const mixedFile = new File(['x'], MIXED_1, { type: 'audio/mpeg', lastModified: 111 })
  const instFile = new File(['x'], INST_1, { type: 'audio/mpeg', lastModified: 222 })
  mockLoadPendingBatch.mockResolvedValue({
    batchId: 'b', rows: [{
      key: 'b:row-1', batchId: 'b', rowId: 'row-1', jobId: 'job-live', artist: 'Eddy Grant', title: 'I Dont Wanna Dance', createdAt: Date.now(),
      files: [
        { fileType: 'audio', identity: MIXED_1, name: MIXED_1, size: 1, lastModified: 111, sessionUri: 'https://session/audio' },
        { fileType: 'existing_instrumental', identity: INST_1, name: INST_1, size: 1, lastModified: 222, sessionUri: 'https://session/inst' },
      ],
    }],
  })
  mockApi.completeJobUpload.mockRejectedValueOnce(new ApiError('Job is not pending', 400))
  ;(mockApi.getJob as jest.Mock).mockResolvedValue({ job_id: 'job-live', status: 'transcribing' })

  render(<TenantBulkFlow onJobsChanged={jest.fn()} />)
  await screen.findByTestId('resume-banner')
  fireEvent.click(screen.getByRole('button', { name: /Choose folder to resume/i }))
  fireEvent.change(screen.getByTestId('bulk-folder-input'), { target: { files: [mixedFile, instFile] } })
  await screen.findAllByLabelText('Artist')
  fireEvent.click(screen.getByRole('button', { name: /Submit 1 tracks/i }))

  await waitFor(() => expect(screen.getByText(/All tracks submitted/i)).toBeInTheDocument())
})

it('a first-time row whose finalize response was lost but job started is not duplicated', async () => {
  // The server accepted uploads-complete but the response was lost; the
  // client's retry of the call then gets a 400 "not pending". The job is
  // processing, so the row is done — no second job.
  const { ApiError } = jest.requireMock('@/lib/api')
  mockApi.analyzeBulk.mockResolvedValue({
    rows: [{ artist: 'Eddy Grant', title: 'I Dont Wanna Dance', mixed_filename: MIXED_1, instrumental_filename: INST_1, confidence: 'high', warning: null }],
    unpaired: [],
    ignored: [],
  })
  mockApi.completeJobUpload.mockRejectedValueOnce(new ApiError('Job is not pending', 400))
  ;(mockApi.getJob as jest.Mock).mockResolvedValue({ status: 'transcribing' })

  render(<TenantBulkFlow onJobsChanged={jest.fn()} />)
  selectFiles()
  await screen.findAllByLabelText('Artist')
  fireEvent.click(screen.getByRole('button', { name: /Submit 1 tracks/i }))

  await waitFor(() => expect(screen.getByText(/All tracks submitted/i)).toBeInTheDocument())
  expect(mockApi.createJobWithUploadUrls).toHaveBeenCalledTimes(1)
})

it('a 400 on a still-pending job keeps the job for retry (no duplicate)', async () => {
  const { ApiError } = jest.requireMock('@/lib/api')
  mockApi.analyzeBulk.mockResolvedValue({
    rows: [{ artist: 'Eddy Grant', title: 'I Dont Wanna Dance', mixed_filename: MIXED_1, instrumental_filename: INST_1, confidence: 'high', warning: null }],
    unpaired: [],
    ignored: [],
  })
  mockApi.completeJobUpload
    .mockRejectedValueOnce(new ApiError('File not uploaded', 400))
    .mockResolvedValueOnce({ status: 'success', message: 'started' })

  render(<TenantBulkFlow onJobsChanged={jest.fn()} />)
  selectFiles()
  await screen.findAllByLabelText('Artist')
  fireEvent.click(screen.getByRole('button', { name: /Submit 1 tracks/i }))
  await waitFor(() => expect(screen.getByText(/File not uploaded/)).toBeInTheDocument())

  fireEvent.click(screen.getByRole('button', { name: /Submit 1 tracks/i }))
  await waitFor(() => expect(mockApi.completeJobUpload).toHaveBeenCalledTimes(2))
  expect(mockApi.createJobWithUploadUrls).toHaveBeenCalledTimes(1)
  const [first, second] = mockApi.completeJobUpload.mock.calls.map(c => c[0])
  expect(second).toBe(first)
})

it('rows queued for retry are not counted as failed in the modal', async () => {
  // Four rows fail, then are retried: only 3 start at once, the 4th waits.
  const rows4 = [1, 2, 3, 4].map(i => ({ artist: 'A', title: `S${i}`, mixed_filename: `m${i}.mp3`, instrumental_filename: `i${i}.mp3`, confidence: 'high', warning: null }))
  mockApi.analyzeBulk.mockResolvedValue({ rows: rows4, unpaired: [], ignored: [] })
  const files = rows4.flatMap(r => [new File(['x'], r.mixed_filename, { type: 'audio/mpeg' }), new File(['x'], r.instrumental_filename, { type: 'audio/mpeg' })])
  mockApi.createJobWithUploadUrls.mockRejectedValue(new Error('backend down'))

  render(<TenantBulkFlow onJobsChanged={jest.fn()} />)
  fireEvent.change(screen.getByTestId('bulk-files-input'), { target: { files } })
  await screen.findAllByLabelText('Artist')
  fireEvent.click(screen.getByRole('button', { name: /Submit 4 tracks/i }))
  await waitFor(() => expect(screen.getAllByText(/will retry on submit/i)).toHaveLength(4))

  // Retry with creation hanging, so rows stay queued / in flight.
  mockApi.createJobWithUploadUrls.mockImplementation(() => new Promise(() => {}))
  fireEvent.click(screen.getByRole('button', { name: /Submit 4 tracks/i }))
  const detail = await screen.findByTestId('upload-progress-detail')
  expect(detail).toHaveTextContent('0 of 4 tracks submitted')
  expect(detail).not.toHaveTextContent('failed')
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
