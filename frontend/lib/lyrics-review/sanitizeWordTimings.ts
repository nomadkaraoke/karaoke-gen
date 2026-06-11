import { LyricsSegment, Word } from './types'

export interface TimingChange {
  wordId: string
  wordText: string
  field: 'start_time' | 'end_time'
  from: number | null
  to: number
}

/**
 * Enforce the timing invariant for one segment:
 *   segment.start <= word.start <= word.end <= segment.end, non-decreasing, finite, >= 0.
 *
 * The segment's own start/end are treated as the authoritative window. Because the
 * editor derives segment bounds from word min/max on every edit (updateSegment), a word
 * outside the segment window only happens via corruption (e.g. a manual-sync glitch),
 * never via a legitimate extension. Returns a NEW segment plus the list of clamps applied
 * so callers can warn. A clean segment returns the same word objects and changes: [].
 */
export function sanitizeSegmentTimings(segment: LyricsSegment): {
  segment: LyricsSegment
  changes: TimingChange[]
} {
  const changes: TimingChange[] = []

  const segStart = isFiniteNumber(segment.start_time) ? (segment.start_time as number) : 0
  const segEndRaw = isFiniteNumber(segment.end_time) ? (segment.end_time as number) : segStart
  const segEnd = Math.max(segEndRaw, segStart)

  let prevEnd = segStart
  const words: Word[] = segment.words.map((w) => {
    const clamp = (v: number) => Math.min(Math.max(v, segStart), segEnd)

    const start = isFiniteNumber(w.start_time) ? (w.start_time as number) : null
    const end = isFiniteNumber(w.end_time) ? (w.end_time as number) : null

    const wantStart = start === null ? prevEnd : start
    const newStart = clamp(Math.max(wantStart, segStart))
    const wantEnd = end === null ? newStart : end
    const newEnd = clamp(Math.max(wantEnd, newStart))

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
