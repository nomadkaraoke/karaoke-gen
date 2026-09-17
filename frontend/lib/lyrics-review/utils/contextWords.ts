import { LyricsSegment, Word } from '@/lib/lyrics-review/types'

/**
 * For each segment, collect the timed words from *other* segments that fall within the
 * padded timeline window `[segment.start - pad, segment.end + pad]`. These are drawn as
 * greyed, read-only neighbour bars in the Waveforms-mode inline timeline (matching the
 * Edit Segment modal's `contextWords`), so the reviewer can see whether a segment should
 * absorb or is colliding with an adjacent one.
 *
 * Returns a Map keyed by segment index. Segments with no timed bounds map to an empty array.
 */
export function computeContextWordsBySegment(
  segments: LyricsSegment[],
  padSeconds: number
): Map<number, Word[]> {
  const result = new Map<number, Word[]>()

  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i]
    if (seg.start_time === null || seg.end_time === null) {
      result.set(i, [])
      continue
    }

    const lo = seg.start_time - padSeconds
    const hi = seg.end_time + padSeconds
    const nearby: Word[] = []

    for (let j = 0; j < segments.length; j++) {
      if (j === i) continue
      for (const w of segments[j].words) {
        if (w.start_time === null || w.end_time === null) continue
        // Intersect the word's span with the padded window.
        if (w.end_time >= lo && w.start_time <= hi) nearby.push(w)
      }
    }

    result.set(i, nearby)
  }

  return result
}
