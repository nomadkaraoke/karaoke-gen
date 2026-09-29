/**
 * Tests for StalledDownloadNotice — the "Keep trying?" prompt shown on a job
 * whose torrent audio download stalled (source offline).
 */

import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { StalledDownloadNotice, isStalledDownload } from '../job/StalledDownloadNotice'
import { Job, api } from '@/lib/api'

jest.mock('@/lib/api', () => ({
  api: {
    retryJob: jest.fn(),
    chooseDifferentAudio: jest.fn(),
  },
}))

const mockToast = jest.fn()
jest.mock('@/hooks/use-toast', () => ({
  useToast: () => ({ toast: mockToast }),
}))

const stalledJob = (details: Record<string, any> = {}): Job => ({
  job_id: 'job-1',
  status: 'failed',
  progress: 12,
  created_at: '2026-09-29T00:00:00Z',
  updated_at: '2026-09-29T00:00:00Z',
  artist: 'Burn The Ballroom',
  title: 'Goodbye Cruel World',
  error_message: 'Audio download failed: Torrent download stalled for 1200s',
  error_details: { stage: 'audio_download', code: 'audio_download_stalled', stall_minutes: 20, keep_trying: false, ...details },
})

describe('isStalledDownload', () => {
  it('is true only for failed jobs with the stalled code', () => {
    expect(isStalledDownload(stalledJob())).toBe(true)
    expect(isStalledDownload({ ...stalledJob(), status: 'downloading_audio' })).toBe(false)
    expect(isStalledDownload({ ...stalledJob(), error_details: { stage: 'audio_download' } })).toBe(false)
    expect(isStalledDownload({ status: 'failed' } as Job)).toBe(false)
  })
})

describe('StalledDownloadNotice', () => {
  const onRefresh = jest.fn()
  const onChooseAudio = jest.fn()

  beforeEach(() => jest.clearAllMocks())

  it('explains the stall with the waited minutes instead of the raw error', () => {
    render(<StalledDownloadNotice job={stalledJob()} onRefresh={onRefresh} onChooseAudio={onChooseAudio} />)
    expect(screen.getByText("Your audio hasn't started downloading after 20 minutes")).toBeInTheDocument()
    expect(screen.getByText(/low availability/)).toBeInTheDocument()
    expect(screen.queryByText(/Torrent download stalled/)).not.toBeInTheDocument()
  })

  it('uses the extended explanation after a keep-trying attempt', () => {
    render(<StalledDownloadNotice job={stalledJob({ stall_minutes: 60, keep_trying: true })}
      onRefresh={onRefresh} onChooseAudio={onChooseAudio} />)
    expect(screen.getByText("Your audio hasn't started downloading after 60 minutes")).toBeInTheDocument()
    expect(screen.getByText(/still offline/)).toBeInTheDocument()
  })

  it('Keep trying retries with keepTrying and refreshes', async () => {
    ;(api.retryJob as jest.Mock).mockResolvedValue({ status: 'success' })
    render(<StalledDownloadNotice job={stalledJob()} onRefresh={onRefresh} onChooseAudio={onChooseAudio} />)
    fireEvent.click(screen.getByText('Keep trying (up to 1 hour)'))
    await waitFor(() => expect(onRefresh).toHaveBeenCalled())
    expect(api.retryJob).toHaveBeenCalledWith('job-1', { keepTrying: true })
    expect(onChooseAudio).not.toHaveBeenCalled()
  })

  it('Choose different audio reopens selection and opens the picker', async () => {
    ;(api.chooseDifferentAudio as jest.Mock).mockResolvedValue({ status: 'success' })
    render(<StalledDownloadNotice job={stalledJob()} onRefresh={onRefresh} onChooseAudio={onChooseAudio} />)
    fireEvent.click(screen.getByText('Choose different audio'))
    await waitFor(() => expect(onChooseAudio).toHaveBeenCalled())
    expect(api.chooseDifferentAudio).toHaveBeenCalledWith('job-1')
    expect(onRefresh).toHaveBeenCalled()
  })

  it('shows an error toast and does not open the picker when the call fails', async () => {
    ;(api.chooseDifferentAudio as jest.Mock).mockRejectedValue(new Error('boom'))
    render(<StalledDownloadNotice job={stalledJob()} onRefresh={onRefresh} onChooseAudio={onChooseAudio} />)
    fireEvent.click(screen.getByText('Choose different audio'))
    await waitFor(() => expect(mockToast).toHaveBeenCalledWith(expect.objectContaining({ variant: 'destructive' })))
    expect(onChooseAudio).not.toHaveBeenCalled()
  })
})
