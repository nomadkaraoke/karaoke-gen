import { useCallback, useMemo, useState } from 'react'
import type {
  CorrectionData,
  EditLog,
  LyricsSegment,
  MissingLyricsMarker,
  VocalGapsResult,
} from '../types'
import { addEditEntry } from '../utils/editLog'
import { applyOffsetToTime } from '../utils/timingUtils'
import {
  findOpenMissingLyricsGaps,
  gapInsertIndex,
  insertMissingLyrics,
  type OpenMissingLyricsGap,
} from '../utils/missingLyrics'

interface UseMissingLyricsOptions {
  /** The backend's analysis (from the loaded correction data; never mutated). */
  vocalGaps: VocalGapsResult | null | undefined
  /** Segments as loaded — the stale-analysis guard checks gaps against these. */
  initialSegments: LyricsSegment[]
  /** The reviewer's CURRENT data (history[historyIndex]). */
  data: CorrectionData
  /** The review's undo/redo-aware data setter. */
  updateDataWithHistory: (newData: CorrectionData, actionDescription?: string) => void
  editLog: EditLog
  isReadOnly: boolean
  /** Display timing offset (ms) — markers are positioned/labelled in display time. */
  timingOffsetMs?: number
}

export interface ResyncLine {
  segment: LyricsSegment
  index: number
}

export interface UseMissingLyricsResult {
  openGaps: OpenMissingLyricsGap[]
  /** Marker rows for TranscriptionView, in DISPLAY time (offset applied). */
  markers: MissingLyricsMarker[]
  /**
   * Insert the gap's reference lines as new segments (one undoable history step).
   * Returns the inserted segments + their index, or null when nothing was inserted
   * (read-only, pending untimed words already there, or no lines).
   */
  insertLines: (gap: OpenMissingLyricsGap) => { insertedAt: number; inserted: LyricsSegment[] } | null
  /** Lines inserted via the callout that still need syncing (present, unsynced). */
  resyncLines: ResyncLine[]
  dismissResync: () => void
}

/** Timing fingerprint, to tell whether an inserted line was re-synced since insertion. */
const timingSignature = (segment: LyricsSegment) =>
  segment.words.map((w) => `${w.start_time}-${w.end_time}`).join('|')

export function useMissingLyrics({
  vocalGaps,
  initialSegments,
  data,
  updateDataWithHistory,
  editLog,
  isReadOnly,
  timingOffsetMs = 0,
}: UseMissingLyricsOptions): UseMissingLyricsResult {
  const segments = data.corrected_segments
  const openGaps = useMemo(
    () => findOpenMissingLyricsGaps(vocalGaps, segments, initialSegments),
    [vocalGaps, segments, initialSegments]
  )

  const markers = useMemo(
    () =>
      openGaps.map(({ id, gap }) => ({
        id,
        beforeSegmentIndex: gapInsertIndex(gap, segments),
        start: applyOffsetToTime(gap.start, timingOffsetMs) as number,
        end: applyOffsetToTime(gap.end, timingOffsetMs) as number,
      })),
    [openGaps, segments, timingOffsetMs]
  )

  // Inserted segment id -> timing signature at insertion. Appended across inserts.
  const [inserted, setInserted] = useState<Record<string, string>>({})

  const insertLines = useCallback(
    (open: OpenMissingLyricsGap) => {
      if (isReadOnly || open.lines.length === 0 || open.pendingSegmentIndex !== null) return null
      const result = insertMissingLyrics(data, open)
      if (result.inserted.length === 0) return null
      if (result.split) {
        addEditEntry(editLog, 'segment_split', {
          segment_id: data.corrected_segments[result.split.index]?.id ?? null,
          segment_index: result.split.index,
          details: { origin: 'missing_lyrics_gap', new_segment_id: result.split.newSegmentId },
        })
      }
      result.inserted.forEach((segment, i) => {
        addEditEntry(editLog, 'segment_add', {
          segment_id: segment.id,
          segment_index: result.insertedAt + i,
          word_ids_after: segment.words.map((w) => w.id),
          text_after: segment.text,
          details: {
            origin: 'missing_lyrics_gap',
            gap_start: open.gap.start,
            gap_end: open.gap.end,
            reference_source: open.source,
            synced_reference: open.synced,
            timed_from_reference: segment.words.every((w) => w.start_time !== null),
          },
        })
      })
      updateDataWithHistory(result.data, 'insert missing lyrics')
      setInserted((prev) => {
        const next = { ...prev }
        for (const s of result.inserted) next[s.id] = timingSignature(s)
        return next
      })
      return { insertedAt: result.insertedAt, inserted: result.inserted }
    },
    [data, editLog, isReadOnly, updateDataWithHistory]
  )

  // Derived: only lines that still exist and haven't been re-synced since insertion
  // (untimed words, or timings unchanged from the reference) stay in the hint.
  const resyncLines = useMemo(() => {
    const out: ResyncLine[] = []
    segments.forEach((segment, index) => {
      const sig = inserted[segment.id]
      if (sig === undefined) return
      const untimed = segment.words.some((w) => w.start_time === null || w.end_time === null)
      if (untimed || timingSignature(segment) === sig) out.push({ segment, index })
    })
    return out
  }, [segments, inserted])

  const dismissResync = useCallback(() => setInserted({}), [])

  return useMemo(
    () => ({ openGaps, markers, insertLines, resyncLines, dismissResync }),
    [openGaps, markers, insertLines, resyncLines, dismissResync]
  )
}
