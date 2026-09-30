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

/**
 * For each segment, the nearest timed word edges in *other* segments: the latest `end_time`
 * among earlier segments (`prevEnd`) and the earliest `start_time` among later segments
 * (`nextStart`). The Waveforms inline timeline clamps word resizes (incl. edge auto-extend) to
 * these so a word can't be stretched into the neighbouring line — even when that line's words
 * are too far away to be drawn as context. `null` when there is no such neighbour.
 */
export function computeNeighbourBoundsBySegment(
  segments: LyricsSegment[]
): Map<number, { prevEnd: number | null; nextStart: number | null }> {
  const result = new Map<number, { prevEnd: number | null; nextStart: number | null }>()
  const timed = (seg: LyricsSegment) =>
    seg.words.filter((w) => w.start_time !== null && w.end_time !== null)

  let prevEnd: number | null = null
  for (let i = 0; i < segments.length; i++) {
    result.set(i, { prevEnd, nextStart: null })
    for (const w of timed(segments[i])) {
      if (prevEnd === null || w.end_time! > prevEnd) prevEnd = w.end_time!
    }
  }

  let nextStart: number | null = null
  for (let i = segments.length - 1; i >= 0; i--) {
    result.get(i)!.nextStart = nextStart
    for (const w of timed(segments[i])) {
      if (nextStart === null || w.start_time! < nextStart) nextStart = w.start_time!
    }
  }

  return result
}
