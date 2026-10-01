import { useCallback, useMemo } from 'react'
import type {
  CorrectionData,
  EditLog,
  LyricsSegment,
  MissingLyricsMarker,
  VocalGapsResult,
} from '../types'
import { addEditEntry } from '../utils/editLog'
import {
  findOpenMissingLyricsGaps,
  gapInsertIndex,
  insertMissingLyrics,
  type OpenMissingLyricsGap,
} from '../utils/missingLyrics'

interface UseMissingLyricsOptions {
  /** The backend's analysis (from the loaded correction data; never mutated). */
  vocalGaps: VocalGapsResult | null | undefined
  /** The reviewer's CURRENT data (history[historyIndex]). */
  data: CorrectionData
  /** The review's undo/redo-aware data setter. */
  updateDataWithHistory: (newData: CorrectionData, actionDescription?: string) => void
  editLog: EditLog
  isReadOnly: boolean
}

export interface UseMissingLyricsResult {
  openGaps: OpenMissingLyricsGap[]
  markers: MissingLyricsMarker[]
  /**
   * Insert the gap's reference lines as new segments (one undoable history step).
   * Returns the inserted segments + their index, or null when nothing was inserted
   * (read-only, or no lines to insert).
   */
  insertLines: (gap: OpenMissingLyricsGap) => { insertedAt: number; inserted: LyricsSegment[] } | null
}

export function useMissingLyrics({
  vocalGaps,
  data,
  updateDataWithHistory,
  editLog,
  isReadOnly,
}: UseMissingLyricsOptions): UseMissingLyricsResult {
  const segments = data.corrected_segments
  const openGaps = useMemo(() => findOpenMissingLyricsGaps(vocalGaps, segments), [vocalGaps, segments])

  const markers = useMemo(
    () =>
      openGaps.map(({ id, gap }) => ({
        id,
        beforeSegmentIndex: gapInsertIndex(gap, segments),
        start: gap.start,
        end: gap.end,
      })),
    [openGaps, segments]
  )

  const insertLines = useCallback(
    (open: OpenMissingLyricsGap) => {
      if (isReadOnly || open.lines.length === 0) return null
      const result = insertMissingLyrics(data, open.gap, open.lines)
      if (result.inserted.length === 0) return null
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
          },
        })
      })
      updateDataWithHistory(result.data, 'insert missing lyrics')
      return { insertedAt: result.insertedAt, inserted: result.inserted }
    },
    [data, editLog, isReadOnly, updateDataWithHistory]
  )

  return { openGaps, markers, insertLines }
}
