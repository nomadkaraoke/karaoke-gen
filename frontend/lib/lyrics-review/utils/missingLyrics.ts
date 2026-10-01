import { nanoid } from 'nanoid'
import type { CorrectionData, LyricsSegment, VocalGap, VocalGapsResult, Word } from '../types'

/**
 * Possible missing lyrics: "evidenced" vocal gaps (the lead vocal is singing, the
 * transcription has no words there, AND reference lyrics place lines there).
 *
 * Everything here is derived from the reviewer's edit state — the backend's
 * `vocal_gaps` payload is never mutated, so a marker goes away as soon as timed words
 * land in the gap and comes back on undo.
 *
 * Rules (all times in the raw segment timeline, i.e. before any display timing offset):
 *
 * - **Edge tolerance.** The backend rounds gap bounds to 2dp and a gap's end IS the next
 *   word's start, so words starting within `GAP_EDGE_TOLERANCE_S` of either edge are the
 *   neighbours, not lyrics inside the gap.
 * - **Stale analysis guard.** The analysis may have run on an older transcription (e.g. the
 *   job was re-transcribed). A gap is only trusted if, in the segments AS LOADED, it holds
 *   no words and is bracketed by words within `GAP_BRACKET_TOLERANCE_S` on each side (or it
 *   touches the song start / has no words after it).
 * - **Open vs pending.** Open = no timed words inside. Pending = no timed words inside but
 *   UNTIMED words sit between the gap's neighbouring timed words in document order (the
 *   reviewer added lyrics there, or inserted them via the callout, but hasn't synced them
 *   yet) → no Insert (would duplicate), just a "sync timing" prompt.
 */

/** Words starting this close to a gap edge are its neighbours (backend rounds to 2dp). */
export const GAP_EDGE_TOLERANCE_S = 0.05
/** Stale-analysis guard: neighbouring words must sit this close to the gap edges at load. */
export const GAP_BRACKET_TOLERANCE_S = 1.0

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
  /**
   * Set when untimed words already sit at the gap's position: index of the segment holding
   * the first of them (to open for Tap To Sync). Insert is not offered in this state.
   */
  pendingSegmentIndex: number | null
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

const isTimed = (w: Word): w is Word & { start_time: number; end_time: number } =>
  w.start_time !== null && w.end_time !== null

/** Starts inside the gap, ignoring words within the edge tolerance (the neighbours). */
const startsInsideGap = (start: number, gap: VocalGap): boolean =>
  start >= gap.start + GAP_EDGE_TOLERANCE_S && start < gap.end - GAP_EDGE_TOLERANCE_S

/** True when any word with a start time begins inside the gap (edge tolerance applied). */
export const gapHasWords = (gap: VocalGap, segments: LyricsSegment[]): boolean =>
  segments.some((seg) =>
    seg.words.some((w) => w.start_time !== null && startsInsideGap(w.start_time, gap))
  )

/**
 * Stale-analysis guard (see module doc): in the segments as loaded, the gap must be empty
 * and bracketed by words near both edges (or touch the song start / have nothing after it).
 */
export const isGapConsistentWithSegments = (gap: VocalGap, segments: LyricsSegment[]): boolean => {
  if (gapHasWords(gap, segments)) return false
  const timed = segments.flatMap((s) => s.words).filter(isTimed)
  const before = timed.filter((w) => w.start_time < gap.start + GAP_EDGE_TOLERANCE_S)
  const after = timed.filter((w) => w.start_time >= gap.end - GAP_EDGE_TOLERANCE_S)
  const prevOk =
    before.length === 0
      ? gap.start <= GAP_BRACKET_TOLERANCE_S
      : Math.abs(Math.max(...before.map((w) => w.end_time)) - gap.start) <= GAP_BRACKET_TOLERANCE_S
  const nextOk =
    after.length === 0 ||
    Math.abs(Math.min(...after.map((w) => w.start_time)) - gap.end) <= GAP_BRACKET_TOLERANCE_S
  return prevOk && nextOk
}

/**
 * Untimed words positioned (in document order) between the gap's neighbouring timed
 * words. Returns the segment index of the first one, or null.
 */
export const pendingUntimedSegmentIndex = (
  gap: VocalGap,
  segments: LyricsSegment[]
): number | null => {
  const flat = segments.flatMap((s, segIdx) => s.words.map((w) => ({ w, segIdx })))
  let prevPos = -1
  let nextPos = flat.length
  flat.forEach(({ w }, pos) => {
    if (w.start_time === null) return
    if (w.start_time < gap.start + GAP_EDGE_TOLERANCE_S) prevPos = Math.max(prevPos, pos)
    else if (w.start_time >= gap.end - GAP_EDGE_TOLERANCE_S) nextPos = Math.min(nextPos, pos)
  })
  for (let pos = prevPos + 1; pos < nextPos; pos++) {
    if (flat[pos].w.start_time === null) return flat[pos].segIdx
  }
  return null
}

/**
 * Evidenced gaps that still have no timed words in the current segments, in time order.
 * `initialSegments` (the segments as loaded) drives the stale-analysis guard.
 */
export const findOpenMissingLyricsGaps = (
  vocalGaps: VocalGapsResult | null | undefined,
  segments: LyricsSegment[],
  initialSegments: LyricsSegment[] = segments
): OpenMissingLyricsGap[] => {
  const gaps = vocalGaps?.gaps ?? []
  return gaps
    .filter(
      (gap) =>
        gap.evidenced &&
        gap.end > gap.start &&
        isGapConsistentWithSegments(gap, initialSegments) &&
        !gapHasWords(gap, segments)
    )
    .sort((a, b) => a.start - b.start)
    .map((gap) => ({
      id: missingLyricsGapId(gap),
      gap,
      ...pickReferenceLines(gap),
      pendingSegmentIndex: pendingUntimedSegmentIndex(gap, segments),
    }))
}

const gapMid = (gap: VocalGap) => (gap.start + gap.end) / 2

const segmentFromWords = (base: LyricsSegment, words: Word[], id = base.id): LyricsSegment => {
  const timed = words.filter(isTimed)
  return {
    ...base,
    id,
    words,
    text: words.map((w) => w.text).join(' '),
    start_time: timed.length ? Math.min(...timed.map((w) => w.start_time)) : null,
    end_time: timed.length ? Math.max(...timed.map((w) => w.end_time)) : null,
  }
}

/**
 * Where lines for this gap belong, based on WORD times (gaps sit between consecutive
 * words, so one can fall inside a segment). If a segment has timed words both before and
 * after the gap it is split there: words before keep the segment, words after move to a
 * new segment with a fresh id (like splitSegment). Pure.
 */
export const planGapInsertion = (
  gap: VocalGap,
  segments: LyricsSegment[]
): { segments: LyricsSegment[]; insertAt: number; split: { index: number; newSegmentId: string } | null } => {
  const mid = gapMid(gap)
  for (let i = 0; i < segments.length; i++) {
    const words = segments[i].words
    const k = words.findIndex((w) => w.start_time !== null && w.start_time >= mid)
    const hasBefore = words.some((w, j) => j < k && w.start_time !== null)
    if (k > 0 && hasBefore) {
      const newSegmentId = nanoid()
      const a = segmentFromWords(segments[i], words.slice(0, k))
      const b = segmentFromWords(segments[i], words.slice(k), newSegmentId)
      return {
        segments: [...segments.slice(0, i), a, b, ...segments.slice(i + 1)],
        insertAt: i + 1,
        split: { index: i, newSegmentId },
      }
    }
  }
  const idx = segments.findIndex((s) => s.words.some((w) => w.start_time !== null && w.start_time >= mid))
  return { segments, insertAt: idx === -1 ? segments.length : idx, split: null }
}

/** Index the gap's lines go at (without splitting) — for marker placement. */
export const gapInsertIndex = (gap: VocalGap, segments: LyricsSegment[]): number => {
  const plan = planGapInsertion(gap, segments)
  // A split puts the marker between the two halves: before the (still unsplit) segment's
  // later words — i.e. after the segment being split.
  return plan.split ? plan.split.index + 1 : plan.insertAt
}

const normalize = (s: string) => s.toLowerCase().replace(/\s+/g, ' ').trim()

/**
 * Build one segment per line.
 *
 * Timing: when `referenceSegments` (the SYNCED reference source, e.g. LRCLIB) has a segment
 * with the same text starting inside the gap and every word timed, its word timings are
 * used. Otherwise the line's words are inserted UNTIMED (start/end null), so the review's
 * "N lyric word(s) have no timing yet" submit guard forces a Tap To Sync. Untimed lines get
 * the gap as their segment bounds so Play / the Edit modal open on the right stretch.
 * No timings are fabricated.
 */
export const buildMissingLyricsSegments = (
  gap: VocalGap,
  lines: string[],
  singer?: LyricsSegment['singer'],
  referenceSegments: LyricsSegment[] | null = null
): LyricsSegment[] => {
  const used = new Set<string>()
  const withSinger = singer !== undefined ? { singer } : {}
  const out: LyricsSegment[] = []
  for (const line of lines) {
    const texts = line.split(/\s+/).filter(Boolean)
    if (texts.length === 0) continue
    const ref = (referenceSegments ?? []).find((r) => {
      if (used.has(r.id) || normalize(r.text) !== normalize(line)) return false
      const first = r.words.find((w) => w.start_time !== null)
      return (
        first !== undefined &&
        first.start_time! >= gap.start &&
        first.start_time! <= gap.end &&
        r.words.length > 0 &&
        r.words.every(isTimed)
      )
    })
    if (ref) {
      used.add(ref.id)
      const words: Word[] = ref.words.map((w) => ({
        id: nanoid(),
        text: w.text,
        start_time: w.start_time,
        end_time: w.end_time,
        confidence: 1.0,
      }))
      out.push({
        id: nanoid(),
        text: words.map((w) => w.text).join(' '),
        words,
        start_time: words[0].start_time,
        end_time: words[words.length - 1].end_time,
        ...withSinger,
      })
    } else {
      const words: Word[] = texts.map((text) => ({
        id: nanoid(),
        text,
        start_time: null,
        end_time: null,
        confidence: 1.0,
      }))
      out.push({
        id: nanoid(),
        text: texts.join(' '),
        words,
        start_time: gap.start,
        end_time: gap.end,
        ...withSinger,
      })
    }
  }
  return out
}

const MIN_WORD_S = 0.05 // a fitted line must leave at least this much per word
const EDGE_PAD_S = 0.05 // keep this clear of the neighbouring lines' words

/**
 * Keep synced-reference timings inside the space the gap actually has: a reference line
 * may run past the next transcribed line (LRCLIB is line-synced; its last line in a gap
 * often ends after the next line starts — job 5710831e: 33.07s vs next line at 32.40s).
 * Each timed line is clamped to [previous line's end, `hi`] and only an overflowing line
 * is compressed (affinely, keeping its word proportions); a line that can't fit sensibly
 * falls back to untimed, so the submit guard forces a Tap To Sync. Pure.
 */
export const fitTimedLinesToWindow = (
  lines: LyricsSegment[],
  lo: number,
  hi: number
): LyricsSegment[] => {
  let cursor = lo
  return lines.map((seg) => {
    if (!seg.words.length || !seg.words.every(isTimed)) return seg
    const t0 = seg.words[0].start_time as number
    const t1 = seg.words[seg.words.length - 1].end_time as number
    const start = Math.max(t0, cursor)
    const end = Math.min(t1, hi)
    if (t1 > t0 && start === t0 && end === t1) {
      cursor = t1
      return seg
    }
    if (end - start < MIN_WORD_S * seg.words.length || t1 <= t0) {
      const words = seg.words.map((w) => ({ ...w, start_time: null, end_time: null }))
      return { ...seg, words, start_time: Math.max(lo, cursor), end_time: hi }
    }
    const k = (end - start) / (t1 - t0)
    const map = (t: number) => Math.round((start + (t - t0) * k) * 1000) / 1000
    const words = seg.words.map((w) => ({
      ...w,
      start_time: map(w.start_time as number),
      end_time: map(w.end_time as number),
    }))
    cursor = end
    return { ...seg, words, start_time: words[0].start_time, end_time: words[words.length - 1].end_time }
  })
}

const lastTimedEnd = (seg?: LyricsSegment): number | null => {
  const timed = (seg?.words ?? []).filter(isTimed)
  return timed.length ? (timed[timed.length - 1].end_time as number) : null
}

const firstTimedStart = (seg?: LyricsSegment): number | null => {
  const timed = (seg?.words ?? []).find(isTimed)
  return timed ? (timed.start_time as number) : null
}

/**
 * Insert the gap's reference lines into `data.corrected_segments` at the right
 * chronological position (splitting a segment that spans the gap). Pure.
 */
export const insertMissingLyrics = (
  data: CorrectionData,
  open: Pick<OpenMissingLyricsGap, 'gap' | 'lines' | 'source' | 'synced'>
): {
  data: CorrectionData
  insertedAt: number
  inserted: LyricsSegment[]
  split: { index: number; newSegmentId: string } | null
} => {
  const plan = planGapInsertion(open.gap, data.corrected_segments)
  // Same convention as addSegmentBefore: prefer the following segment's singer, then the
  // preceding one's.
  const following = plan.segments[plan.insertAt]
  const preceding = plan.segments[plan.insertAt - 1]
  const singer = following?.singer ?? preceding?.singer
  const referenceSegments =
    open.synced && open.source ? data.reference_lyrics?.[open.source]?.segments ?? null : null
  const built = buildMissingLyricsSegments(open.gap, open.lines, singer, referenceSegments)
  // Window the inserted lines may occupy: after the preceding line's last word, before
  // the following line's first word (and within the gap).
  const lo = Math.max(open.gap.start, (lastTimedEnd(preceding) ?? -Infinity) + EDGE_PAD_S)
  const hi = Math.min(open.gap.end, (firstTimedStart(following) ?? Infinity) - EDGE_PAD_S)
  const inserted = fitTimedLinesToWindow(built, lo, hi)
  if (inserted.length === 0) {
    return { data, insertedAt: plan.insertAt, inserted, split: null }
  }
  const corrected_segments = [
    ...plan.segments.slice(0, plan.insertAt),
    ...inserted,
    ...plan.segments.slice(plan.insertAt),
  ]
  return { data: { ...data, corrected_segments }, insertedAt: plan.insertAt, inserted, split: plan.split }
}

/** m:ss (floored) for the callout's time range. */
export const formatGapTime = (seconds: number): string => {
  const s = Math.max(0, Math.floor(seconds))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}
