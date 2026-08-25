import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ReviewChangesModal from '../modals/ReviewChangesModal'
import type { CorrectionData } from '@/lib/lyrics-review/types'

// Mock PreviewVideoSection since it has complex dependencies
jest.mock('../PreviewVideoSection', () => ({
  __esModule: true,
  default: () => <div data-testid="preview-video">Preview</div>,
}))

function makeData(overrides: Partial<CorrectionData> = {}): CorrectionData {
  return {
    original_segments: [],
    reference_lyrics: {},
    anchor_sequences: [],
    gap_sequences: [],
    resized_segments: [],
    corrections_made: 0,
    confidence: 1,
    corrections: [],
    corrected_segments: [],
    metadata: {},
    ...overrides,
  } as CorrectionData
}

describe('ReviewChangesModal', () => {
  const defaultProps = {
    open: true,
    onClose: jest.fn(),
    onSubmit: jest.fn(),
  }

  it('disables submit button when there are no lyrics (0 segments)', () => {
    render(
      <ReviewChangesModal
        {...defaultProps}
        data={makeData({ corrected_segments: [] })}
      />
    )

    const submitButton = screen.getByRole('button', { name: /proceed to instrumental/i })
    expect(submitButton).toBeDisabled()
  })

  it('shows warning message when there are no lyrics', () => {
    render(
      <ReviewChangesModal
        {...defaultProps}
        data={makeData({ corrected_segments: [] })}
      />
    )

    expect(screen.getByText('No lyrics detected')).toBeInTheDocument()
    expect(screen.getByText(/no lyrics were found in the audio/i)).toBeInTheDocument()
    expect(screen.getByText(/replace all/i)).toBeInTheDocument()
  })

  it('enables submit button when segments exist', () => {
    const segments = [{ text: 'Hello', words: [], start_time: 0, end_time: 1 }]
    render(
      <ReviewChangesModal
        {...defaultProps}
        data={makeData({ corrected_segments: segments as any })}
      />
    )

    const submitButton = screen.getByRole('button', { name: /proceed to instrumental/i })
    expect(submitButton).not.toBeDisabled()
  })

  it('does not show warning when segments exist', () => {
    const segments = [{ text: 'Hello', words: [], start_time: 0, end_time: 1 }]
    render(
      <ReviewChangesModal
        {...defaultProps}
        data={makeData({ corrected_segments: segments as any })}
      />
    )

    expect(screen.queryByText('No lyrics detected')).not.toBeInTheDocument()
    expect(screen.getByText(/total segments: 1/i)).toBeInTheDocument()
  })

  it('calls onSubmit when button clicked with valid segments', async () => {
    const user = userEvent.setup()
    const onSubmit = jest.fn()
    const segments = [{ text: 'Hello', words: [], start_time: 0, end_time: 1 }]

    render(
      <ReviewChangesModal
        {...defaultProps}
        onSubmit={onSubmit}
        data={makeData({ corrected_segments: segments as any })}
      />
    )

    await user.click(screen.getByRole('button', { name: /proceed to instrumental/i }))
    expect(onSubmit).toHaveBeenCalledTimes(1)
  })

  it('shows "Complete Track" CTA when the review completes directly (no instrumental step)', () => {
    const segments = [{ text: 'Hello', words: [], start_time: 0, end_time: 1 }]
    render(
      <ReviewChangesModal
        {...defaultProps}
        completesReview
        data={makeData({ corrected_segments: segments as any })}
      />
    )

    expect(screen.getByRole('button', { name: /complete track/i })).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: /proceed to instrumental/i })
    ).not.toBeInTheDocument()
  })
})
