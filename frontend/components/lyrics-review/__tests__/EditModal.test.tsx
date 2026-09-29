import { fireEvent, render, screen } from '@testing-library/react'
import EditModal from '../modals/EditModal'
import { LyricsSegment } from '@/lib/lyrics-review/types'

// Mock complex sub-components that have their own heavy dependencies
jest.mock('../EditWordList', () => ({
  __esModule: true,
  default: () => <div data-testid="edit-word-list">EditWordList</div>,
}))

jest.mock('../EditTimelineSection', () => ({
  __esModule: true,
  // Exposes the timeline's Ctrl-click delete callback as a button so tests can drive it.
  default: ({ onWordDelete }: { onWordDelete?: (index: number) => void }) => (
    <div data-testid="edit-timeline-section">
      <button data-testid="ctrl-delete-first-word" onClick={() => onWordDelete?.(0)} />
    </div>
  ),
}))

// Mock sonner toast to avoid jsdom issues
jest.mock('sonner', () => ({
  toast: {
    warning: jest.fn(),
    error: jest.fn(),
    success: jest.fn(),
  },
}))

// Mock useAudioReady to avoid window event listeners
jest.mock('@/lib/lyrics-review/hooks/useAudioReady', () => ({
  useAudioReady: () => ({ ready: true, progress: 1 }),
}))

// Mock setModalHandler to avoid global keyboard state side-effects
jest.mock('@/lib/lyrics-review/utils/keyboardHandlers', () => ({
  setModalHandler: jest.fn(),
}))

const cleanWord = { id: 'e', text: 'beer,', start_time: 16.1, end_time: 16.42 }

const badSegment: LyricsSegment = {
  id: 's1',
  text: 'A whiskey and a beer,',
  start_time: 15.18,
  end_time: 18.04,
  words: [
    { id: 'a', text: 'A', start_time: 0, end_time: -0.005 },
    { id: 'b', text: 'whiskey', start_time: 0, end_time: -0.005 },
    { id: 'e', text: 'beer,', start_time: 16.1, end_time: 16.42 },
  ],
}

const cleanSegment: LyricsSegment = {
  ...badSegment,
  words: [cleanWord],
}

function renderModal(segment: LyricsSegment, onSave: (s: LyricsSegment) => void = () => {}) {
  return render(
    <EditModal
      open
      segment={segment}
      segmentIndex={0}
      originalSegment={segment}
      onClose={() => {}}
      onSave={onSave}
    />
  )
}

it('shows the timing-repaired banner when a segment opens with out-of-bounds words', () => {
  renderModal(badSegment)
  expect(screen.getByTestId('timing-sanitized-banner')).toBeInTheDocument()
})

it('shows no banner for a clean segment', () => {
  renderModal(cleanSegment)
  expect(screen.queryByTestId('timing-sanitized-banner')).not.toBeInTheDocument()
})

it('Ctrl-clicking a word in the timeline removes it from the edited segment', () => {
  const onSave = jest.fn()
  const twoWords: LyricsSegment = {
    ...cleanSegment,
    text: 'cold beer,',
    words: [{ id: 'd', text: 'cold', start_time: 15.5, end_time: 16.0 }, cleanWord],
  }
  renderModal(twoWords, onSave)
  fireEvent.click(screen.getByTestId('ctrl-delete-first-word'))
  fireEvent.click(screen.getByRole('button', { name: /save/i }))
  expect(onSave).toHaveBeenCalledWith(
    expect.objectContaining({ text: 'beer,', words: [cleanWord], start_time: 16.1 })
  )
})
