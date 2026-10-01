import { useCallback, useState } from 'react'
import { renderHook, act } from '@testing-library/react'
import { useMissingLyrics } from '../useMissingLyrics'
import { createEditLog } from '../../utils/editLog'
import { countUntimedWords } from '../../utils/timingCompleteness'
import type { CorrectionData, LyricsSegment, VocalGap, VocalGapsResult } from '../../types'

const seg = (id: string, start: number, end: number, text: string): LyricsSegment => ({
  id,
  text,
  start_time: start,
  end_time: end,
  words: [{ id: `${id}-w`, text, start_time: start, end_time: end }],
})

const evidenced = (start: number, end: number, lines: string[]): VocalGap => ({
  start,
  end,
  duration: end - start,
  active_fraction: 0.9,
  longest_run_s: end - start - 0.5,
  reference_lines: { genius: lines },
  synced_reference_lines: {},
  suspect: true,
  evidenced: true,
})

const vocalGaps: VocalGapsResult = {
  version: '0.2.0',
  gaps: [
    evidenced(5, 11, ['line one', 'line two words']),
    { ...evidenced(20, 26, []), evidenced: false },
    evidenced(30, 40, ['late line']),
  ],
}

const baseData = {
  corrected_segments: [seg('s1', 0, 5, 'intro'), seg('s2', 11, 20, 'verse'), seg('s3', 26, 30, 'bridge'), seg('s4', 40, 45, 'outro')],
  reference_lyrics: {},
} as unknown as CorrectionData

/**
 * Mirrors LyricsAnalyzer's history machinery (history array + index, push on update,
 * undo = step the index back) so the test proves insertion is one undoable step.
 */
function useHarness({ isReadOnly = false, timingOffsetMs = 0 } = {}) {
  const [history, setHistory] = useState<CorrectionData[]>([baseData])
  const [index, setIndex] = useState(0)
  const data = history[index]
  const updateDataWithHistory = useCallback(
    (next: CorrectionData) => {
      const h = history.slice(0, index + 1)
      h.push(JSON.parse(JSON.stringify(next)))
      setHistory(h)
      setIndex(h.length - 1)
    },
    [history, index]
  )
  const [editLog] = useState(() => createEditLog('job', 'hash'))
  const missing = useMissingLyrics({
    vocalGaps,
    initialSegments: baseData.corrected_segments,
    data,
    updateDataWithHistory,
    editLog,
    isReadOnly,
    timingOffsetMs,
  })
  return {
    missing,
    data,
    undo: () => setIndex((i) => Math.max(0, i - 1)),
    historyLength: history.length,
    editLog,
    setData: updateDataWithHistory,
  }
}

describe('useMissingLyrics', () => {
  it('exposes only evidenced open gaps, with markers at the chronological position', () => {
    const { result } = renderHook(() => useHarness())
    expect(result.current.missing.openGaps.map((g) => g.gap.start)).toEqual([5, 30])
    expect(result.current.missing.markers[0]).toEqual({
      id: 'missing-lyrics-5.00-11.00',
      beforeSegmentIndex: 1,
      start: 5,
      end: 11,
    })
  })

  it('markers use display time when a timing offset is set', () => {
    const { result } = renderHook(() => useHarness({ timingOffsetMs: 500 }))
    expect(result.current.missing.markers[0]).toMatchObject({ start: 5.5, end: 11.5 })
  })

  it('inserts through the history (one undoable step); untimed lines leave the gap pending; undo restores', () => {
    const { result } = renderHook(() => useHarness())
    let ret: ReturnType<typeof result.current.missing.insertLines> = null
    act(() => {
      ret = result.current.missing.insertLines(result.current.missing.openGaps[0])
    })
    expect(ret!.insertedAt).toBe(1)
    expect(result.current.historyLength).toBe(2)
    expect(result.current.data.corrected_segments.map((s) => s.text)).toEqual([
      'intro',
      'line one',
      'line two words',
      'verse',
      'bridge',
      'outro',
    ])
    // untimed → the submit guard will force a sync
    expect(countUntimedWords(result.current.data.corrected_segments)).toBe(5)
    // gap is now pending: still listed but Insert is refused
    const pending = result.current.missing.openGaps[0]
    expect(pending.pendingSegmentIndex).toBe(1)
    let again: unknown = 'unset'
    act(() => {
      again = result.current.missing.insertLines(pending)
    })
    expect(again).toBeNull()
    expect(result.current.historyLength).toBe(2)

    const adds = result.current.editLog.entries.filter((e) => e.operation === 'segment_add')
    expect(adds).toHaveLength(2)
    expect(adds[0].details).toMatchObject({ origin: 'missing_lyrics_gap', reference_source: 'genius', timed_from_reference: false })

    act(() => result.current.undo())
    expect(result.current.data.corrected_segments).toHaveLength(4)
    expect(result.current.missing.openGaps[0].pendingSegmentIndex).toBeNull()
  })

  it('re-sync hint APPENDS across inserts and drops lines once synced or deleted', () => {
    const { result } = renderHook(() => useHarness())
    act(() => {
      result.current.missing.insertLines(result.current.missing.openGaps[0])
    })
    act(() => {
      result.current.missing.insertLines(result.current.missing.openGaps.find((g) => g.gap.start === 30)!)
    })
    expect(result.current.missing.resyncLines.map((l) => l.segment.text)).toEqual([
      'line one',
      'line two words',
      'late line',
    ])

    // Sync "line one" (timed words), delete "late line"
    act(() => {
      const segs = result.current.data.corrected_segments
        .filter((s) => s.text !== 'late line')
        .map((s) =>
          s.text === 'line one'
            ? {
                ...s,
                start_time: 6,
                end_time: 7,
                words: s.words.map((w, i) => ({ ...w, start_time: 6 + i * 0.5, end_time: 6.5 + i * 0.5 })),
              }
            : s
        )
      result.current.setData({ ...result.current.data, corrected_segments: segs })
    })
    expect(result.current.missing.resyncLines.map((l) => l.segment.text)).toEqual(['line two words'])
    expect(result.current.missing.resyncLines[0].index).toBe(2)

    act(() => result.current.missing.dismissResync())
    expect(result.current.missing.resyncLines).toEqual([])
  })

  it('does not insert in read-only mode', () => {
    const { result } = renderHook(() => useHarness({ isReadOnly: true }))
    let ret: unknown = 'unset'
    act(() => {
      ret = result.current.missing.insertLines(result.current.missing.openGaps[0])
    })
    expect(ret).toBeNull()
    expect(result.current.historyLength).toBe(1)
  })

  it('returns a stable object between renders when nothing changed (safe useCallback dep)', () => {
    const { result, rerender } = renderHook(() => useHarness())
    const first = result.current.missing
    rerender()
    expect(result.current.missing).toBe(first)
    expect(result.current.missing.insertLines).toBe(first.insertLines)
  })
})
