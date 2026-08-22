import type { LyricsSegment } from '@/lib/lyrics-review/types'

/**
 * Count words that lack timing (start_time or end_time == null) across all
 * segments. Untimed lyrics are a legitimate *intermediate* state in the
 * synchronizer (paste lyrics → tap-sync), but they must be resolved before the
 * video is generated: the render pipeline (segment resizing, ASS generation, the
 * GCE encoder) does arithmetic on timing values and crashes on null.
 *
 * Mirrors the backend gate `validate_segment_timing`
 * (karaoke_gen/lyrics_transcriber/output/timing_validation.py) so the UI can
 * warn the user *before* they submit rather than surfacing a server error.
 *
 * A segment with no words is skipped (nothing to time).
 */
export function countUntimedWords(segments: LyricsSegment[]): number {
  let count = 0
  for (const segment of segments) {
    const words = segment.words ?? []
    if (words.length === 0) continue
    const segmentUntimed = segment.start_time === null || segment.end_time === null
    for (const word of words) {
      if (word.start_time === null || word.end_time === null) {
        count += 1
      } else if (segmentUntimed) {
        // A timed word inside a segment with null bounds still can't render.
        count += 1
      }
    }
  }
  return count
}

/** True if any segment/word lacks timing. */
export function hasUntimedLyrics(segments: LyricsSegment[]): boolean {
  return countUntimedWords(segments) > 0
}
