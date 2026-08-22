import { LyricsSegment, Word } from './types'

export interface TimingChange {
  wordId: string
  wordText: string
  field: 'start_time' | 'end_time'
  from: number | null
  to: number
}

/**
 * Clamp word timings for one segment into the segment's authoritative window.
 *
 * Behaviour:
 *   - If `segment.start_time` or `segment.end_time` is null/NaN/Infinity the segment
 *     bounds are unknown, so the segment is returned **untouched** with `changes: []`.
 *     (The editor derives segment bounds from word min/max on every edit; corrupted
 *     segment bounds are a separate concern.)
 *   - Each word's `start_time` and `end_time` are clamped into
 *     `[segment.start_time, segment.end_time]`.
 *   - Words whose `start_time` is missing or non-finite are placed at the previous
 *     word's (repaired) end time, keeping repaired words in order within the window.
 *   - `word.end_time` is forced `>= word.start_time` (after clamping).
 *   - Already-valid in-bounds word timings are left as-is; only out-of-window or
 *     missing values are corrected.
 *
 * These semantics intentionally mirror the shipped backend
 * `SegmentResizer._sanitize_segment_timings`. Returns a NEW segment object plus the
 * list of clamps applied so callers can warn. A clean segment returns the same word
 * objects and `changes: []`.
 */
export function sanitizeSegmentTimings(segment: LyricsSegment): {
  segment: LyricsSegment
  changes: TimingChange[]
} {
  // Guard: if segment bounds are non-finite we cannot clamp words — leave untouched.
  if (!isFiniteNumber(segment.start_time) || !isFiniteNumber(segment.end_time)) {
    return { segment, changes: [] }
  }

  const changes: TimingChange[] = []

  const segStart = segment.start_time as number
  const segEnd = Math.max(segment.end_time as number, segStart)

  // Hoist clamp once — segStart/segEnd are constant for the whole segment.
  const clamp = (v: number) => Math.min(Math.max(v, segStart), segEnd)

  let prevEnd = segStart
  const words: Word[] = segment.words.map((w) => {
    const start = isFiniteNumber(w.start_time) ? (w.start_time as number) : null
    const end   = isFiniteNumber(w.end_time)   ? (w.end_time   as number) : null

    // Invalid start = non-finite OR outside the segment window -> place at previous word's end.
    const newStart = start !== null && start >= segStart && start <= segEnd
      ? start
      : clamp(Math.max(prevEnd, segStart))
    // Invalid end = non-finite, before start, or past segment end.
    let newEnd: number
    if (end !== null && end >= newStart && end <= segEnd) {
      newEnd = end
    } else {
      newEnd = clamp(Math.max(newStart, end ?? newStart))
      if (newEnd < newStart) newEnd = newStart
    }

    let next = w
    if (newStart !== start) {
      changes.push({ wordId: w.id, wordText: w.text, field: 'start_time', from: w.start_time, to: newStart })
      next = { ...next, start_time: newStart }
    }
    if (newEnd !== end) {
      changes.push({ wordId: w.id, wordText: w.text, field: 'end_time', from: w.end_time, to: newEnd })
      next = { ...next, end_time: newEnd }
    }
    prevEnd = newEnd
    return next
  })

  if (changes.length === 0) return { segment, changes }
  return { segment: { ...segment, words }, changes }
}

function isFiniteNumber(v: number | null | undefined): boolean {
  return typeof v === 'number' && Number.isFinite(v)
}
