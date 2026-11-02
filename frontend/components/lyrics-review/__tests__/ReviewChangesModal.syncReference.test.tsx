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
  return { generatePreviewVideo: jest.fn(), getPreviewVideoStatus: jest.fn(), getPreviewVideoUrl: jest.fn() }
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

  it('passes the mark with notes to onSubmit when an admin ticks it', async () => {
    setRole('admin')
    const onSubmit = jest.fn()
    render(<ReviewChangesModal open onClose={jest.fn()} onSubmit={onSubmit} data={data} apiClient={client()} />)
    await userEvent.click(screen.getByTestId('sync-reference-checkbox'))
    await userEvent.type(screen.getByTestId('sync-reference-notes'), 'spot-checked')
    await userEvent.click(screen.getByRole('button', { name: /proceed to instrumental/i }))
    expect(onSubmit).toHaveBeenCalledWith({ syncReference: { verified: true, notes: 'spot-checked' } })
  })

  it('sends no mark when the admin leaves it unticked', async () => {
    setRole('admin')
    const onSubmit = jest.fn()
    render(<ReviewChangesModal open onClose={jest.fn()} onSubmit={onSubmit} data={data} apiClient={client()} />)
    expect(screen.getByTestId('sync-reference')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /proceed to instrumental/i }))
    expect(onSubmit).toHaveBeenCalledWith()
  })

  it('starts ticked from a saved mark and sends verified=false when revoked', async () => {
    setRole('admin')
    const onSubmit = jest.fn()
    const marked = { ...data, sync_reference: { verified: true, notes: 'old note' } } as CorrectionData
    render(<ReviewChangesModal open onClose={jest.fn()} onSubmit={onSubmit} data={marked} apiClient={client()} />)
    const box = screen.getByTestId('sync-reference-checkbox') as HTMLInputElement
    expect(box.checked).toBe(true)
    await userEvent.click(box)
    await userEvent.click(screen.getByRole('button', { name: /proceed to instrumental/i }))
    expect(onSubmit).toHaveBeenCalledWith({ syncReference: { verified: false, notes: 'old note' } })
  })

  it('sends verified=false once touched, even with no saved mark', async () => {
    setRole('admin')
    const onSubmit = jest.fn()
    render(<ReviewChangesModal open onClose={jest.fn()} onSubmit={onSubmit} data={data} apiClient={client()} />)
    const box = screen.getByTestId('sync-reference-checkbox')
    await userEvent.click(box)
    await userEvent.click(box)
    await userEvent.click(screen.getByRole('button', { name: /proceed to instrumental/i }))
    expect(onSubmit).toHaveBeenCalledWith({ syncReference: { verified: false, notes: undefined } })
  })
})
