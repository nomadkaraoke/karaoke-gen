import { render, screen } from '@testing-library/react'
import EditModal from '../modals/EditModal'
import { LyricsSegment } from '@/lib/lyrics-review/types'

// Mock complex sub-components that have their own heavy dependencies
jest.mock('../EditWordList', () => ({
  __esModule: true,
  default: () => <div data-testid="edit-word-list">EditWordList</div>,
}))

jest.mock('../EditTimelineSection', () => ({
  __esModule: true,
  default: () => <div data-testid="edit-timeline-section">EditTimelineSection</div>,
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

function renderModal(segment: LyricsSegment) {
  return render(
    <EditModal
      open
      segment={segment}
      segmentIndex={0}
      originalSegment={segment}
      onClose={() => {}}
      onSave={() => {}}
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
