import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ReviewChangesModal from '../modals/ReviewChangesModal'
import type { CorrectionData } from '@/lib/lyrics-review/types'
import { useAuth } from '@/lib/auth'

jest.mock('../PreviewVideoSection', () => {
  const React = require('react')
  return {
    __esModule: true,
    default: React.forwardRef(function MockPreview(_props: unknown, ref: unknown) {
      React.useImperativeHandle(ref, () => ({ auditionInstrumental: jest.fn() }))
      return <div data-testid="preview-video">Preview</div>
    }),
  }
})

const data = {
  original_segments: [], reference_lyrics: {}, anchor_sequences: [], gap_sequences: [],
  resized_segments: [], corrections_made: 0, confidence: 1, corrections: [], metadata: {},
  corrected_segments: [{ text: 'Hello', words: [], start_time: 0, end_time: 1 }],
} as unknown as CorrectionData

function client() {
  return {
    generatePreviewVideo: jest.fn(),
    getPreviewVideoStatus: jest.fn(),
    getPreviewVideoUrl: jest.fn(),
    setSyncReference: jest.fn().mockResolvedValue(undefined),
  }
}

function setRole(role: string | null) {
  useAuth.setState({ user: role ? ({ email: 'a@nomadkaraoke.com', role } as any) : null })
}

describe('ReviewChangesModal sync-reference mark (admin only)', () => {
  afterEach(() => setRole(null))

  it('is hidden for non-admin users', () => {
    setRole('user')
    render(<ReviewChangesModal open onClose={jest.fn()} onSubmit={jest.fn()} data={data} apiClient={client()} />)
    expect(screen.queryByTestId('sync-reference')).not.toBeInTheDocument()
  })

  it('records the mark with notes when an admin ticks it and submits', async () => {
    setRole('admin')
    const api = client()
    const onSubmit = jest.fn()
    render(<ReviewChangesModal open onClose={jest.fn()} onSubmit={onSubmit} data={data} apiClient={api} />)
    await userEvent.click(screen.getByTestId('sync-reference-checkbox'))
    await userEvent.type(screen.getByTestId('sync-reference-notes'), 'spot-checked')
    await userEvent.click(screen.getByRole('button', { name: /proceed to instrumental/i }))
    expect(api.setSyncReference).toHaveBeenCalledWith(true, 'spot-checked')
    expect(onSubmit).toHaveBeenCalled()
  })

  it('sends nothing when the admin leaves it unticked', async () => {
    setRole('admin')
    const api = client()
    const onSubmit = jest.fn()
    render(<ReviewChangesModal open onClose={jest.fn()} onSubmit={onSubmit} data={data} apiClient={api} />)
    expect(screen.getByTestId('sync-reference')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /proceed to instrumental/i }))
    expect(api.setSyncReference).not.toHaveBeenCalled()
    expect(onSubmit).toHaveBeenCalled()
  })

  it('still submits the review when saving the mark fails', async () => {
    setRole('admin')
    const api = client()
    api.setSyncReference.mockRejectedValue(new Error('network'))
    const onSubmit = jest.fn()
    const err = jest.spyOn(console, 'error').mockImplementation(() => {})
    render(<ReviewChangesModal open onClose={jest.fn()} onSubmit={onSubmit} data={data} apiClient={api} />)
    await userEvent.click(screen.getByTestId('sync-reference-checkbox'))
    await userEvent.click(screen.getByRole('button', { name: /proceed to instrumental/i }))
    expect(onSubmit).toHaveBeenCalled()
    err.mockRestore()
  })
})
