import { nanoid } from 'nanoid'
import type { CorrectionData, LyricsSegment, VocalGap, VocalGapsResult, Word } from '../types'

/**
 * Possible missing lyrics: "evidenced" vocal gaps (the lead vocal is singing, the
 * transcription has no words there, AND reference lyrics place lines there) that
 * still contain no words in the reviewer's CURRENT segments.
 *
 * Everything here is derived from the current edit state — the backend's
 * `vocal_gaps` payload is never mutated, so a marker disappears as soon as words
 * land in the gap (inserted via the callout or added by hand) and comes back on undo.
 */

/** Keep provisional timings this far inside the gap so they don't butt into neighbours. */
export const MISSING_LYRICS_EDGE_PAD_S = 0.1

export interface OpenMissingLyricsGap {
  /** Stable id for React keys / test ids (derived from the gap's times). */
  id: string
  gap: VocalGap
  /** Reference source the lines came from. */
  source: string | null
  /** The reference lines expected in the gap (may be empty if none survived trimming). */
  lines: string[]
  /** True when `lines` came from `synced_reference_lines` (timestamped inside the gap). */
  synced: boolean
}

export const missingLyricsGapId = (gap: VocalGap): string =>
  `missing-lyrics-${gap.start.toFixed(2)}-${gap.end.toFixed(2)}`

const firstNonEmpty = (
  bySource: Record<string, string[]> | undefined
): { source: string; lines: string[] } | null => {
  for (const [source, raw] of Object.entries(bySource ?? {})) {
    const lines = (raw ?? []).map((l) => (l ?? '').trim()).filter(Boolean)
    if (lines.length > 0) return { source, lines }
  }
  return null
}

/** Prefer synced reference lines (timestamped inside the gap), else the anchor-bounded ones. */
export const pickReferenceLines = (
  gap: VocalGap
): { source: string | null; lines: string[]; synced: boolean } => {
  const synced = firstNonEmpty(gap.synced_reference_lines)
  if (synced) return { ...synced, synced: true }
  const plain = firstNonEmpty(gap.reference_lines)
  if (plain) return { ...plain, synced: false }
  return { source: null, lines: [], synced: false }
}

/** True when any timed word in `segments` starts inside [gap.start, gap.end). */
export const gapHasWords = (gap: VocalGap, segments: LyricsSegment[]): boolean =>
  segments.some((seg) =>
    seg.words.some(
      (w) => w.start_time !== null && w.start_time >= gap.start && w.start_time < gap.end
    )
  )

/** Evidenced gaps that still have no words in the current segments, in time order. */
export const findOpenMissingLyricsGaps = (
  vocalGaps: VocalGapsResult | null | undefined,
  segments: LyricsSegment[]
): OpenMissingLyricsGap[] => {
  const gaps = vocalGaps?.gaps ?? []
  return gaps
    .filter((gap) => gap.evidenced && gap.end > gap.start && !gapHasWords(gap, segments))
    .sort((a, b) => a.start - b.start)
    .map((gap) => ({ id: missingLyricsGapId(gap), gap, ...pickReferenceLines(gap) }))
}

/**
 * Index in `segments` where a segment for this gap belongs chronologically: before the
 * first timed segment that starts at/after the gap's midpoint (untimed segments are
 * skipped when comparing). Returns `segments.length` to append.
 */
export const gapInsertIndex = (gap: VocalGap, segments: LyricsSegment[]): number => {
  const mid = (gap.start + gap.end) / 2
  const idx = segments.findIndex((s) => s.start_time !== null && s.start_time >= mid)
  return idx === -1 ? segments.length : idx
}

const round3 = (t: number) => Math.round(t * 1000) / 1000

/**
 * Build one segment per line, words split on whitespace, with provisional timings
 * spread evenly (per word) across [start + pad, end - pad]. These are placeholders for
 * the reviewer to re-sync (Tap To Sync), not a timing model.
 */
export const buildMissingLyricsSegments = (
  gap: VocalGap,
  lines: string[],
  singer?: LyricsSegment['singer']
): LyricsSegment[] => {
  const wordLines = lines
    .map((line) => line.split(/\s+/).filter(Boolean))
    .filter((ws) => ws.length > 0)
  const totalWords = wordLines.reduce((n, ws) => n + ws.length, 0)
  if (totalWords === 0) return []

  const pad = gap.end - gap.start > 4 * MISSING_LYRICS_EDGE_PAD_S ? MISSING_LYRICS_EDGE_PAD_S : 0
  const t0 = gap.start + pad
  const slot = (gap.end - pad - t0) / totalWords

  let k = 0
  return wordLines.map((ws) => {
    const words: Word[] = ws.map((text) => {
      const start = round3(t0 + k * slot)
      const end = round3(t0 + (k + 1) * slot)
      k += 1
      return {
        id: nanoid(),
        text,
        start_time: start,
        end_time: end,
        confidence: 1.0,
      }
    })
    return {
      id: nanoid(),
      text: ws.join(' '),
      words,
      start_time: words[0].start_time,
      end_time: words[words.length - 1].end_time,
      ...(singer !== undefined ? { singer } : {}),
    }
  })
}

/**
 * Insert the gap's reference lines into `data.corrected_segments` at the right
 * chronological position. Pure: returns new data plus the inserted segment range.
 */
export const insertMissingLyrics = (
  data: CorrectionData,
  gap: VocalGap,
  lines: string[]
): { data: CorrectionData; insertedAt: number; inserted: LyricsSegment[] } => {
  const segments = data.corrected_segments
  const insertedAt = gapInsertIndex(gap, segments)
  // Inherit the neighbouring line's singer (duets), like "Add segment" does.
  const neighbour = segments[insertedAt - 1] ?? segments[insertedAt]
  const inserted = buildMissingLyricsSegments(gap, lines, neighbour?.singer)
  const corrected_segments = [
    ...segments.slice(0, insertedAt),
    ...inserted,
    ...segments.slice(insertedAt),
  ]
  return { data: { ...data, corrected_segments }, insertedAt, inserted }
}

/** m:ss (floored) for the callout's time range. */
export const formatGapTime = (seconds: number): string => {
  const s = Math.max(0, Math.floor(seconds))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}
