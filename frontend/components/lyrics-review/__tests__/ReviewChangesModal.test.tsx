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
      React.useImperativeHandle(ref, () => ({ auditionInstrumental: jest.fn() }))
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

  const bothStems = [
    { id: 'clean', label: 'Clean', audio_url: 'http://x/clean.ogg' },
    { id: 'with_backing', label: 'Backing', audio_url: 'http://x/backing.ogg' },
  ]
  const waveformApiClient = () => ({
    generatePreviewVideo: jest.fn(),
    getPreviewVideoStatus: jest.fn(),
    getPreviewVideoUrl: jest.fn(),
    getWaveformData: jest.fn().mockResolvedValue({ amplitudes: [0.1, 0.2], duration_seconds: 10 }),
  })

  it('renders the backing-vocals waveform when backing is the current selection', () => {
    const segments = [{ text: 'Hello', words: [], start_time: 0, end_time: 1 }]
    const apiClient = waveformApiClient()
    render(
      <ReviewChangesModal
        {...defaultProps}
        apiClient={apiClient as any}
        completesReview
        offerInlineChoice
        autoConfident
        recommendedSelection="with_backing"
        currentSelection="with_backing"
        data={makeData({ corrected_segments: segments as any, instrumental_options: bothStems as any })}
      />
    )
    expect(screen.getByText(/click to hear this part/i)).toBeInTheDocument()
    expect(apiClient.getWaveformData).toHaveBeenCalled()
  })

  it('does not render the backing-vocals waveform when the backing stem is unavailable', () => {
    const segments = [{ text: 'Hello', words: [], start_time: 0, end_time: 1 }]
    const apiClient = waveformApiClient()
    render(
      <ReviewChangesModal
        {...defaultProps}
        apiClient={apiClient as any}
        completesReview
        autoConfident
        recommendedSelection="clean"
        currentSelection="clean"
        data={makeData({
          corrected_segments: segments as any,
          instrumental_options: [{ id: 'clean', label: 'Clean', audio_url: 'http://x/clean.ogg' }] as any,
        })}
      />
    )
    expect(screen.queryByText(/click to hear this part/i)).not.toBeInTheDocument()
    expect(apiClient.getWaveformData).not.toHaveBeenCalled()
  })

  it('does not render the backing-vocals waveform when clean is the current selection', () => {
    const segments = [{ text: 'Hello', words: [], start_time: 0, end_time: 1 }]
    const apiClient = waveformApiClient()
    render(
      <ReviewChangesModal
        {...defaultProps}
        apiClient={apiClient as any}
        completesReview
        offerInlineChoice
        currentSelection="clean"
        recommendedSelection="with_backing"
        data={makeData({ corrected_segments: segments as any, instrumental_options: bothStems as any })}
      />
    )
    expect(screen.queryByText(/click to hear this part/i)).not.toBeInTheDocument()
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
    expect(screen.queryByRole('button', { name: /proceed to instrumental/i })).not.toBeInTheDocument()
  })

  it('shows the chooser with a green "recommended" badge + backing note when confident', () => {
    const segments = [{ text: 'Hello', words: [], start_time: 0, end_time: 1 }]
    render(
      <ReviewChangesModal
        {...defaultProps}
        completesReview
        offerInlineChoice
        autoConfident
        recommendedSelection="with_backing"
        currentSelection="with_backing"
        data={makeData({ corrected_segments: segments as any, instrumental_options: bothStems as any })}
      />
    )
    expect(screen.getByRole('button', { name: /complete track/i })).toBeInTheDocument()
    expect(screen.getByText(/your karaoke video will use/i)).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /instrumental \+ backing vocals/i })).toBeChecked()
    expect(screen.getByText(/recommended/i)).toBeInTheDocument()
    expect(screen.queryByText(/^suggested$/i)).not.toBeInTheDocument()
    expect(screen.getByText(/backing vocals in the instrumental/i)).toBeInTheDocument()
    expect(
      screen.getByRole('radio', { name: /advanced mode \(edit backing vocals or upload your own instrumental\)/i })
    ).toBeInTheDocument()
  })

  it('shows the chooser with a preselected default and no badge when NOT confident', () => {
    const segments = [{ text: 'Hello', words: [], start_time: 0, end_time: 1 }]
    render(
      <ReviewChangesModal
        {...defaultProps}
        completesReview
        offerInlineChoice
        recommendedSelection="with_backing"
        currentSelection="with_backing"
        data={makeData({ corrected_segments: segments as any, instrumental_options: bothStems as any })}
      />
    )
    expect(screen.getByRole('radio', { name: /instrumental \+ backing vocals/i })).toBeChecked()
    expect(screen.getByRole('radio', { name: /^clean instrumental$/i })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: /advanced mode/i })).toBeInTheDocument()
    // No "recommended" badge when the scorer wasn't confident — just the neutral
    // prompt and a preselected default.
    expect(screen.queryByText(/recommended/i)).not.toBeInTheDocument()
    expect(screen.getByText(/have a listen and choose/i)).toBeInTheDocument()
  })

  it('checks the clean radio when clean is the current selection', () => {
    const segments = [{ text: 'Hello', words: [], start_time: 0, end_time: 1 }]
    render(
      <ReviewChangesModal
        {...defaultProps}
        completesReview
        offerInlineChoice
        autoConfident
        recommendedSelection="with_backing"
        currentSelection="clean"
        data={makeData({ corrected_segments: segments as any, instrumental_options: bothStems as any })}
      />
    )
    expect(screen.getByRole('radio', { name: /^clean instrumental$/i })).toBeChecked()
    expect(screen.getByText(/you've chosen the clean instrumental/i)).toBeInTheDocument()
  })

  it('selecting the clean radio reports the choice and clears the review-anyway flag', async () => {
    const user = userEvent.setup()
    const onSelectInstrumental = jest.fn()
    const onToggleReviewInstrumental = jest.fn()
    const segments = [{ text: 'Hello', words: [], start_time: 0, end_time: 1 }]
    render(
      <ReviewChangesModal
        {...defaultProps}
        completesReview
        offerInlineChoice
        recommendedSelection="with_backing"
        currentSelection="with_backing"
        onSelectInstrumental={onSelectInstrumental}
        onToggleReviewInstrumental={onToggleReviewInstrumental}
        data={makeData({ corrected_segments: segments as any, instrumental_options: bothStems as any })}
      />
    )
    await user.click(screen.getByRole('radio', { name: /^clean instrumental$/i }))
    expect(onSelectInstrumental).toHaveBeenCalledWith('clean')
    expect(onToggleReviewInstrumental).toHaveBeenCalledWith(false)
  })

  it('selecting the backing radio reports with_backing', async () => {
    const user = userEvent.setup()
    const onSelectInstrumental = jest.fn()
    const segments = [{ text: 'Hello', words: [], start_time: 0, end_time: 1 }]
    render(
      <ReviewChangesModal
        {...defaultProps}
        completesReview
        offerInlineChoice
        recommendedSelection="clean"
        currentSelection="clean"
        onSelectInstrumental={onSelectInstrumental}
        data={makeData({ corrected_segments: segments as any, instrumental_options: bothStems as any })}
      />
    )
    await user.click(screen.getByRole('radio', { name: /instrumental \+ backing vocals/i }))
    expect(onSelectInstrumental).toHaveBeenCalledWith('with_backing')
  })

  it('uses the clean-context Advanced-mode wording when clean is recommended', () => {
    const segments = [{ text: 'Hello', words: [], start_time: 0, end_time: 1 }]
    render(
      <ReviewChangesModal
        {...defaultProps}
        completesReview
        offerInlineChoice
        autoConfident
        recommendedSelection="clean"
        currentSelection="clean"
        data={makeData({ corrected_segments: segments as any, instrumental_options: bothStems as any })}
      />
    )
    expect(
      screen.getByRole('radio', { name: /advanced mode \(review or upload a custom instrumental\)/i })
    ).toBeInTheDocument()
    expect(screen.getByText(/we'll use the clean instrumental/i)).toBeInTheDocument()
  })

  it('selecting Advanced mode calls onToggleReviewInstrumental', async () => {
    const user = userEvent.setup()
    const onToggleReviewInstrumental = jest.fn()
    const segments = [{ text: 'Hello', words: [], start_time: 0, end_time: 1 }]
    render(
      <ReviewChangesModal
        {...defaultProps}
        completesReview
        offerInlineChoice
        recommendedSelection="with_backing"
        currentSelection="with_backing"
        onToggleReviewInstrumental={onToggleReviewInstrumental}
        data={makeData({ corrected_segments: segments as any, instrumental_options: bothStems as any })}
      />
    )
    await user.click(screen.getByRole('radio', { name: /advanced mode/i }))
    expect(onToggleReviewInstrumental).toHaveBeenCalledWith(true)
  })

  it('hides the chooser when neither confident nor both stems are available', () => {
    const segments = [{ text: 'Hello', words: [], start_time: 0, end_time: 1 }]
    render(
      <ReviewChangesModal
        {...defaultProps}
        data={makeData({ corrected_segments: segments as any })}
      />
    )
    expect(screen.queryByRole('radio')).not.toBeInTheDocument()
  })
})
