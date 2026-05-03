import { render, screen } from '@testing-library/react'
import LyricsSynchronizer from '../synchronizer/LyricsSynchronizer'
import type { LyricsSegment } from '@/lib/lyrics-review/types'

// Stub heavy children so the test focuses on the SyncControls disabled state.
jest.mock('../synchronizer/TimelineCanvas', () => () => null)
jest.mock('../synchronizer/UpcomingWordsBar', () => () => null)

function makeSegment(words: Array<{ text: string; start: number | null; end: number | null }>): LyricsSegment {
  return {
    id: 'seg-1',
    text: words.map((w) => w.text).join(' '),
    start_time: words[0]?.start ?? null,
    end_time: words[words.length - 1]?.end ?? null,
    words: words.map((w, i) => ({
      id: `w-${i}`,
      text: w.text,
      start_time: w.start,
      end_time: w.end,
    })),
  }
}

describe('LyricsSynchronizer — Unsync from Cursor button', () => {
  const baseProps = {
    onPlaySegment: jest.fn(),
    onSave: jest.fn(),
    onCancel: jest.fn(),
    setModalSpacebarHandler: jest.fn(),
  }

  // Synced words at 5s and 18s
  const segments = [
    makeSegment([
      { text: 'first', start: 5, end: 5.5 },
      { text: 'second', start: 18, end: 18.5 },
    ]),
  ]

  it('enables button when currentTime is before some synced words', () => {
    render(<LyricsSynchronizer {...baseProps} segments={segments} currentTime={10} />)
    expect(screen.getByRole('button', { name: /unsync from cursor/i })).toBeEnabled()
  })

  it('disables button when currentTime is past all synced words', () => {
    render(<LyricsSynchronizer {...baseProps} segments={segments} currentTime={20} />)
    expect(screen.getByRole('button', { name: /unsync from cursor/i })).toBeDisabled()
  })

  // Regression: previously canUnsyncFromCursor read currentTimeRef.current inside
  // useMemo, which is one frame stale. When currentTime jumped backwards (user
  // scrubbed) the cached value was wrong and the button stayed disabled even
  // though synced words existed after the cursor.
  it('re-enables the button after currentTime jumps backwards past synced words', () => {
    const { rerender } = render(
      <LyricsSynchronizer {...baseProps} segments={segments} currentTime={20} />
    )
    expect(screen.getByRole('button', { name: /unsync from cursor/i })).toBeDisabled()

    rerender(<LyricsSynchronizer {...baseProps} segments={segments} currentTime={10} />)
    expect(screen.getByRole('button', { name: /unsync from cursor/i })).toBeEnabled()
  })
})
