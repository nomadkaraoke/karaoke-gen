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
        data={makeData({
          corrected_segments: segments as any,
          instrumental_options: [
            { id: 'clean', label: 'Clean', audio_url: 'http://x/clean.ogg' },
            { id: 'with_backing', label: 'Backing', audio_url: 'http://x/backing.ogg' },
          ] as any,
        })}
      />
    )
    // Hint text from BackingVocalsWaveform
    expect(screen.getByText(/click to hear this part/i)).toBeInTheDocument()
    expect(apiClient.getWaveformData).toHaveBeenCalled()
  })

  it('does not render the backing-vocals waveform when the backing stem URL is unavailable', () => {
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
        autoInstrumentalSelection="with_backing"
        data={makeData({
          corrected_segments: segments as any,
          instrumental_options: [{ id: 'clean', label: 'Clean', audio_url: 'http://x/clean.ogg' }] as any,
        })}
      />
    )
    expect(screen.queryByText(/click to hear this part/i)).not.toBeInTheDocument()
    expect(apiClient.getWaveformData).not.toHaveBeenCalled()
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

  // Per-screen skip (C1): backing decision auto-resolved. The final-output choice
  // is a single radio group ("Your karaoke video will use:") whose recommended
  // option is the auto-selected instrumental; "Advanced mode" is the escape hatch.
  it('shows the final-output radio group + backing note when backing is confident', () => {
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
    expect(screen.getByText(/your karaoke video will use/i)).toBeInTheDocument()
    // Recommended option = backing; escape hatch = Advanced mode.
    const backingRadio = screen.getByRole('radio', { name: /instrumental \+ backing vocals/i })
    expect(backingRadio).toBeChecked()
    expect(screen.getByText(/backing vocals in the instrumental/i)).toBeInTheDocument()
    expect(
      screen.getByRole('radio', { name: /advanced mode \(edit backing vocals or upload your own instrumental\)/i })
    ).toBeInTheDocument()
  })

  it('offers a Clean instrumental radio only when a clean stem exists alongside backing', () => {
    const segments = [{ text: 'Hello', words: [], start_time: 0, end_time: 1 }]
    render(
      <ReviewChangesModal
        {...defaultProps}
        completesReview
        autoInstrumentalConfident
        autoInstrumentalSelection="with_backing"
        data={makeData({
          corrected_segments: segments as any,
          instrumental_options: [
            { id: 'clean', label: 'Clean', audio_url: 'http://x/clean.ogg' },
            { id: 'with_backing', label: 'Backing', audio_url: 'http://x/backing.ogg' },
          ] as any,
        })}
      />
    )
    expect(screen.getByRole('radio', { name: /^clean instrumental$/i })).toBeInTheDocument()
  })

  it('reflects the clean choice in the note when the reviewer selects the clean radio', () => {
    // The decision state is owned by LyricsAnalyzer and passed via cleanOverride;
    // selecting clean marks that radio and swaps the note.
    const segments = [{ text: 'Hello', words: [], start_time: 0, end_time: 1 }]
    const options = [
      { id: 'clean', label: 'Clean', audio_url: 'http://x/clean.ogg' },
      { id: 'with_backing', label: 'Backing', audio_url: 'http://x/backing.ogg' },
    ]
    const { rerender } = render(
      <ReviewChangesModal
        {...defaultProps}
        completesReview
        autoInstrumentalConfident
        autoInstrumentalSelection="with_backing"
        data={makeData({ corrected_segments: segments as any, instrumental_options: options as any })}
      />
    )
    expect(screen.getByText(/backing vocals in the instrumental/i)).toBeInTheDocument()

    rerender(
      <ReviewChangesModal
        {...defaultProps}
        completesReview
        autoInstrumentalConfident
        autoInstrumentalSelection="with_backing"
        cleanOverride
        data={makeData({ corrected_segments: segments as any, instrumental_options: options as any })}
      />
    )
    expect(screen.getByRole('radio', { name: /^clean instrumental$/i })).toBeChecked()
    expect(screen.getByText(/you've chosen the clean instrumental/i)).toBeInTheDocument()
  })

  it('selecting the clean radio reports the choice and clears the review-anyway flag', async () => {
    const user = userEvent.setup()
    const onInstrumentalChoiceChange = jest.fn()
    const onToggleReviewInstrumental = jest.fn()
    const segments = [{ text: 'Hello', words: [], start_time: 0, end_time: 1 }]
    render(
      <ReviewChangesModal
        {...defaultProps}
        completesReview
        autoInstrumentalConfident
        autoInstrumentalSelection="with_backing"
        onInstrumentalChoiceChange={onInstrumentalChoiceChange}
        onToggleReviewInstrumental={onToggleReviewInstrumental}
        data={makeData({
          corrected_segments: segments as any,
          instrumental_options: [
            { id: 'clean', label: 'Clean', audio_url: 'http://x/clean.ogg' },
            { id: 'with_backing', label: 'Backing', audio_url: 'http://x/backing.ogg' },
          ] as any,
        })}
      />
    )
    await user.click(screen.getByRole('radio', { name: /^clean instrumental$/i }))
    expect(onInstrumentalChoiceChange).toHaveBeenCalledWith('clean')
    expect(onToggleReviewInstrumental).toHaveBeenCalledWith(false)
  })

  it('uses the clean-case Advanced-mode wording when clean is already the default', () => {
    const segments = [{ text: 'Hello', words: [], start_time: 0, end_time: 1 }]
    render(
      <ReviewChangesModal
        {...defaultProps}
        completesReview
        autoInstrumentalConfident
        autoInstrumentalSelection="clean"
        data={makeData({
          corrected_segments: segments as any,
          instrumental_options: [
            { id: 'clean', label: 'Clean', audio_url: 'http://x/clean.ogg' },
          ] as any,
        })}
      />
    )
    expect(
      screen.getByRole('radio', { name: /advanced mode \(review or upload a custom instrumental\)/i })
    ).toBeInTheDocument()
    // No clean-override radio when clean is already the recommended default.
    expect(screen.queryByRole('radio', { name: /^clean instrumental$/i })).not.toBeInTheDocument()
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
        autoInstrumentalConfident
        autoInstrumentalSelection="with_backing"
        onToggleReviewInstrumental={onToggleReviewInstrumental}
        data={makeData({ corrected_segments: segments as any })}
      />
    )
    await user.click(screen.getByRole('radio', { name: /advanced mode/i }))
    expect(onToggleReviewInstrumental).toHaveBeenCalledWith(true)
  })

  it('hides the final-output radio group when backing is not confident', () => {
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
