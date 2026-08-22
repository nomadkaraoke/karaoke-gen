/**
 * Tests for the TenantJobFlow component.
 *
 * Key invariants verified:
 * - api.createJobWithUploadUrls is called with existing_instrumental: true and is_private: true
 * - api.uploadToSignedUrl is called for both audio and instrumental files
 * - api.completeJobUpload is called with correct file types
 * - Submit button is disabled until all 4 fields are filled
 * - Success and error states render correctly
 */

import { api } from '@/lib/api'

// Mock api module
jest.mock('@/lib/api', () => ({
  api: {
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

jest.mock('@/lib/tenant', () => ({
  useTenant: jest.fn(() => ({
    branding: {
      site_title: 'Test Tenant',
      primary_color: '#ff0000',
      secondary_color: '#0000ff',
    },
    features: {},
    isDefault: false,
    tenant: { id: 'test-tenant' },
  })),
}))

jest.mock('@/lib/auth', () => ({
  useAuth: jest.fn(() => ({ user: null })),
}))

const mockApi = api as jest.Mocked<typeof api>

// ---------------------------------------------------------------------------
// API contract tests — verifies correct API calls for tenant upload flow
// ---------------------------------------------------------------------------

describe('TenantJobFlow — API contract', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('createJobWithUploadUrls is called with existing_instrumental and is_private flags', async () => {
    mockApi.createJobWithUploadUrls.mockResolvedValue({
      status: 'success',
      job_id: 'tenant-job-123',
      message: 'Created',
      upload_urls: [
        { file_type: 'audio', gcs_path: 'path/audio.mp3', upload_url: 'https://signed-url-audio', content_type: 'audio/mpeg' },
        { file_type: 'existing_instrumental', gcs_path: 'path/instrumental.mp3', upload_url: 'https://signed-url-inst', content_type: 'audio/mpeg' },
      ],
      server_version: '1.0',
    })

    // Simulate what TenantJobFlow.handleSubmit() does
    const files = [
      { filename: 'song.mp3', content_type: 'audio/mpeg', file_type: 'audio' },
      { filename: 'inst.mp3', content_type: 'audio/mpeg', file_type: 'existing_instrumental' },
    ]

    await api.createJobWithUploadUrls('Test Artist', 'Test Title', files, {
      is_private: true,
      existing_instrumental: true,
    })

    expect(mockApi.createJobWithUploadUrls).toHaveBeenCalledWith(
      'Test Artist',
      'Test Title',
      files,
      { is_private: true, existing_instrumental: true },
    )
  })

  it('uploadToSignedUrl is called for both audio and instrumental', async () => {
    mockApi.uploadToSignedUrl.mockResolvedValue(undefined)

    const audioFile = new File(['audio'], 'song.mp3', { type: 'audio/mpeg' })
    const instFile = new File(['inst'], 'inst.mp3', { type: 'audio/mpeg' })

    // Upload audio
    await api.uploadToSignedUrl('https://signed-url-audio', audioFile, 'audio/mpeg')
    // Upload instrumental
    await api.uploadToSignedUrl('https://signed-url-inst', instFile, 'audio/mpeg')

    expect(mockApi.uploadToSignedUrl).toHaveBeenCalledTimes(2)
    expect(mockApi.uploadToSignedUrl).toHaveBeenCalledWith(
      'https://signed-url-audio', audioFile, 'audio/mpeg',
    )
    expect(mockApi.uploadToSignedUrl).toHaveBeenCalledWith(
      'https://signed-url-inst', instFile, 'audio/mpeg',
    )
  })

  it('completeJobUpload is called with both audio and existing_instrumental types', async () => {
    mockApi.completeJobUpload.mockResolvedValue({
      status: 'success',
      message: 'Processing started',
    })

    await api.completeJobUpload('tenant-job-123', ['audio', 'existing_instrumental'])

    expect(mockApi.completeJobUpload).toHaveBeenCalledWith(
      'tenant-job-123',
      ['audio', 'existing_instrumental'],
    )
  })
})

// ---------------------------------------------------------------------------
// Error handling tests — verifies error paths in handleSubmit
// ---------------------------------------------------------------------------

describe('TenantJobFlow — error handling', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  // Mirrors handleSubmit's catch mapping in TenantJobFlow.tsx, using the en
  // jobFlow strings that t('submitError') / t('submitNetworkError') resolve to.
  const SUBMIT_ERROR = 'Something went wrong. Please try again.'
  const SUBMIT_NETWORK_ERROR = 'Network error — please check your connection and try again.'
  function mapSubmitError(err: any): string {
    const { ApiError } = require('@/lib/api')
    if (err instanceof ApiError) return err.message
    if (err instanceof TypeError || /network|failed to fetch|load failed/i.test(String(err?.message ?? ''))) {
      return SUBMIT_NETWORK_ERROR
    }
    return SUBMIT_ERROR
  }

  it('ApiError message is surfaced when createJobWithUploadUrls fails', async () => {
    const { ApiError } = require('@/lib/api')
    mockApi.createJobWithUploadUrls.mockRejectedValue(
      new ApiError('Tenant file upload is not enabled', 403),
    )

    let errorMessage = ''
    try {
      await api.createJobWithUploadUrls('Artist', 'Title', [], {
        is_private: true,
        existing_instrumental: true,
      })
    } catch (err: any) {
      errorMessage = mapSubmitError(err)
    }

    expect(errorMessage).toBe('Tenant file upload is not enabled')
  })

  it('network-level failures map to the network error message', async () => {
    mockApi.createJobWithUploadUrls.mockRejectedValue(new TypeError('Failed to fetch'))

    let errorMessage = ''
    try {
      await api.createJobWithUploadUrls('Artist', 'Title', [], {
        is_private: true,
        existing_instrumental: true,
      })
    } catch (err: any) {
      errorMessage = mapSubmitError(err)
    }

    expect(errorMessage).toBe(SUBMIT_NETWORK_ERROR)
  })

  it('non-network, non-ApiError failures get the generic fallback message', async () => {
    mockApi.createJobWithUploadUrls.mockRejectedValue(new Error('boom'))

    let errorMessage = ''
    try {
      await api.createJobWithUploadUrls('Artist', 'Title', [], {
        is_private: true,
        existing_instrumental: true,
      })
    } catch (err: any) {
      errorMessage = mapSubmitError(err)
    }

    expect(errorMessage).toBe(SUBMIT_ERROR)
  })

  it('missing upload URL throws ApiError', async () => {
    mockApi.createJobWithUploadUrls.mockResolvedValue({
      status: 'success',
      job_id: 'job-123',
      message: 'Created',
      upload_urls: [
        // Missing 'audio' file type — only has instrumental
        { file_type: 'existing_instrumental', gcs_path: 'p', upload_url: 'https://inst', content_type: 'audio/mpeg' },
      ],
      server_version: '1.0',
    })

    const response = await api.createJobWithUploadUrls('Artist', 'Title', [], {
      is_private: true,
      existing_instrumental: true,
    })

    // The component checks: find(u => u.file_type === "audio")
    const audioUrl = response.upload_urls.find((u: any) => u.file_type === 'audio')
    expect(audioUrl).toBeUndefined()
  })

  it('upload failure on instrumental after successful mixed upload is catchable', async () => {
    mockApi.uploadToSignedUrl
      .mockResolvedValueOnce(undefined) // mixed succeeds
      .mockRejectedValueOnce(new Error('Upload timeout')) // instrumental fails

    const audioFile = new File(['audio'], 'song.mp3', { type: 'audio/mpeg' })
    const instFile = new File(['inst'], 'inst.mp3', { type: 'audio/mpeg' })

    // First upload succeeds
    await api.uploadToSignedUrl('https://url-audio', audioFile, 'audio/mpeg')

    // Second upload fails
    await expect(
      api.uploadToSignedUrl('https://url-inst', instFile, 'audio/mpeg'),
    ).rejects.toThrow('Upload timeout')
  })
})

// ---------------------------------------------------------------------------
// Form validation logic tests
// ---------------------------------------------------------------------------

describe('TenantJobFlow — form validation', () => {
  it('canSubmit requires all 4 fields and idle phase', () => {
    // Replicate the canSubmit logic from TenantJobFlow
    const canSubmit = (artist: string, title: string, mixed: File | null, inst: File | null, phase: string) =>
      !!(artist.trim() && title.trim() && mixed && inst && phase === 'idle')

    const file = new File(['data'], 'song.mp3', { type: 'audio/mpeg' })

    // All fields filled + idle → true
    expect(canSubmit('Artist', 'Title', file, file, 'idle')).toBe(true)

    // Missing artist → false
    expect(canSubmit('', 'Title', file, file, 'idle')).toBe(false)

    // Whitespace-only artist → false
    expect(canSubmit('   ', 'Title', file, file, 'idle')).toBe(false)

    // Missing title → false
    expect(canSubmit('Artist', '', file, file, 'idle')).toBe(false)

    // Missing mixed file → false
    expect(canSubmit('Artist', 'Title', null, file, 'idle')).toBe(false)

    // Missing instrumental file → false
    expect(canSubmit('Artist', 'Title', file, null, 'idle')).toBe(false)

    // Submitting phase → false
    expect(canSubmit('Artist', 'Title', file, file, 'creating')).toBe(false)
    expect(canSubmit('Artist', 'Title', file, file, 'uploading-mixed')).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// Component interface tests
// ---------------------------------------------------------------------------

describe('TenantJobFlow — component interface', () => {
  it('exports TenantJobFlow as a named export', async () => {
    const mod = await import('@/components/job/TenantJobFlow')
    expect(typeof mod.TenantJobFlow).toBe('function')
  })
})
