import { LyricsSegment, TranscriptionViewMode, Word } from '@/lib/lyrics-review/types'

/**
 * Rebuild a segment from an edited word list: refresh the joined text and recompute the
 * segment's start/end from the min/max of the words' timings (null when no word is timed).
 * Mirrors the Edit Segment modal's `updateSegment`, shared by the Waveforms inline editor.
 */
export function recomputeSegmentFromWords(
  segment: LyricsSegment,
  words: Word[]
): LyricsSegment {
  const starts = words.map((w) => w.start_time).filter((n): n is number => n !== null)
  const ends = words.map((w) => w.end_time).filter((n): n is number => n !== null)

  return {
    ...segment,
    words,
    text: words.map((w) => w.text).join(' '),
    start_time: starts.length > 0 ? Math.min(...starts) : null,
    end_time: ends.length > 0 ? Math.max(...ends) : null,
  }
}

/**
 * Resolve the initial Synced Lyrics view mode from localStorage, migrating the legacy
 * boolean key (`lyricsReviewAdvancedMode`) to the new enum (`lyricsReviewViewMode`).
 * Pure and storage-injectable so it can be unit-tested without a real window.
 */
export function resolveInitialViewMode(
  storage: Pick<Storage, 'getItem'> | null | undefined
): TranscriptionViewMode {
  if (!storage) return 'simple'
  const stored = storage.getItem('lyricsReviewViewMode')
  if (stored === 'simple' || stored === 'advanced' || stored === 'waveforms') return stored
  return storage.getItem('lyricsReviewAdvancedMode') === 'true' ? 'advanced' : 'simple'
}
