import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import ReviewChangesModal from '../modals/ReviewChangesModal'
import type { CorrectionData } from '@/lib/lyrics-review/types'

// Mock PreviewVideoSection since it has complex dependencies. It's a
// forwardRef component, so the mock must forward the ref too (the modal
// attaches one for the backing-vocals seek handle).
jest.mock('../PreviewVideoSection', () => {
  const React = require('react')
  return {
    __esModule: true,
    default: React.forwardRef(function MockPreview(_props: unknown, ref: unknown) {
      React.useImperativeHandle(ref, () => ({ switchToInstrumentalAndSeek: jest.fn() }))
      return <div data-testid="preview-video">Preview</div>
    }),
  }
})

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
  })

  it('uses a neutral "Preview Video" title (not "With Vocals")', () => {
    render(
      <ReviewChangesModal
        {...defaultProps}
        data={makeData({ corrected_segments: [] })}
      />
    )
    expect(screen.getByText('Preview Video')).toBeInTheDocument()
    expect(screen.queryByText(/with vocals/i)).not.toBeInTheDocument()
  })

  it('no longer shows the removed "no manual corrections" / "total segments" text', () => {
    const segments = [{ text: 'Hello', words: [], start_time: 0, end_time: 1 }]
    render(
      <ReviewChangesModal
        {...defaultProps}
        data={makeData({ corrected_segments: segments as any, corrections: [] })}
      />
    )
    expect(screen.queryByText(/no manual corrections/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/total segments/i)).not.toBeInTheDocument()
  })

  it('shows the manual-corrections note only when the user made edits', () => {
    const segments = [{ text: 'Hello', words: [], start_time: 0, end_time: 1 }]
    render(
      <ReviewChangesModal
        {...defaultProps}
        data={makeData({
          corrected_segments: segments as any,
          corrections: [{ handler: 'ManualCorrector' } as any],
        })}
      />
    )
    expect(screen.getByText(/manual corrections detected/i)).toBeInTheDocument()
  })

  it('renders the backing-vocals waveform when backing is kept and waveform data is available', () => {
    const segments = [{ text: 'Hello', words: [], start_time: 0, end_time: 1 }]
    const apiClient = {
      generatePreviewVideo: jest.fn(),
      getPreviewVideoStatus: jest.fn(),
      getPreviewVideoUrl: jest.fn(),
      getWaveformData: jest.fn().mockResolvedValue({ amplitudes: [0.1, 0.2], duration_seconds: 10 }),
    }
    render(
      <ReviewChangesModal
        {...defaultProps}
        apiClient={apiClient as any}
        completesReview
        autoInstrumentalConfident
        autoInstrumentalSelection="with_backing"
        data={makeData({ corrected_segments: segments as any })}
      />
    )
    // Hint text from BackingVocalsWaveform
    expect(screen.getByText(/click to hear this part/i)).toBeInTheDocument()
    expect(apiClient.getWaveformData).toHaveBeenCalled()
  })

  it('does not render the backing-vocals waveform when the clean instrumental was chosen', () => {
    const segments = [{ text: 'Hello', words: [], start_time: 0, end_time: 1 }]
    const apiClient = {
      generatePreviewVideo: jest.fn(),
      getPreviewVideoStatus: jest.fn(),
      getPreviewVideoUrl: jest.fn(),
      getWaveformData: jest.fn().mockResolvedValue({ amplitudes: [0.1], duration_seconds: 10 }),
    }
    render(
      <ReviewChangesModal
        {...defaultProps}
        apiClient={apiClient as any}
        completesReview
        autoInstrumentalConfident
        autoInstrumentalSelection="clean"
        data={makeData({ corrected_segments: segments as any })}
      />
    )
    expect(screen.queryByText(/click to hear this part/i)).not.toBeInTheDocument()
    expect(apiClient.getWaveformData).not.toHaveBeenCalled()
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

  // Per-screen skip (C1): backing decision auto-resolved.
  it('shows the auto-instrumental note + escape-hatch checkbox when backing is confident', () => {
    const segments = [{ text: 'Hello', words: [], start_time: 0, end_time: 1 }]
    render(
      <ReviewChangesModal
        {...defaultProps}
        completesReview
        autoInstrumentalConfident
        autoInstrumentalSelection="with_backing"
        data={makeData({ corrected_segments: segments as any })}
      />
    )
    expect(screen.getByRole('button', { name: /complete track/i })).toBeInTheDocument()
    expect(screen.getByText(/keep the backing vocals/i)).toBeInTheDocument()
    expect(screen.getByRole('checkbox')).toBeInTheDocument()
  })

  it('shows the clean-instrumental note when the resolved selection is clean', () => {
    const segments = [{ text: 'Hello', words: [], start_time: 0, end_time: 1 }]
    render(
      <ReviewChangesModal
        {...defaultProps}
        completesReview
        autoInstrumentalConfident
        autoInstrumentalSelection="clean"
        data={makeData({ corrected_segments: segments as any })}
      />
    )
    expect(screen.getByText(/clean instrumental/i)).toBeInTheDocument()
  })

  it('toggling the escape hatch calls onToggleReviewInstrumental', async () => {
    const user = userEvent.setup()
    const onToggleReviewInstrumental = jest.fn()
    const segments = [{ text: 'Hello', words: [], start_time: 0, end_time: 1 }]
    render(
      <ReviewChangesModal
        {...defaultProps}
        completesReview
        autoInstrumentalConfident
        autoInstrumentalSelection="with_backing"
        onToggleReviewInstrumental={onToggleReviewInstrumental}
        data={makeData({ corrected_segments: segments as any })}
      />
    )
    await user.click(screen.getByRole('checkbox'))
    expect(onToggleReviewInstrumental).toHaveBeenCalledWith(true)
  })

  it('hides the auto-instrumental note when backing is not confident', () => {
    const segments = [{ text: 'Hello', words: [], start_time: 0, end_time: 1 }]
    render(
      <ReviewChangesModal
        {...defaultProps}
        data={makeData({ corrected_segments: segments as any })}
      />
    )
    expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  })
})
