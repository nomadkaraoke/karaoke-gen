import { useCallback, useState } from 'react'
import { renderHook, act } from '@testing-library/react'
import { useMissingLyrics } from '../useMissingLyrics'
import { createEditLog } from '../../utils/editLog'
import type { CorrectionData, LyricsSegment, VocalGapsResult } from '../../types'

const seg = (id: string, start: number, end: number, text: string): LyricsSegment => ({
  id,
  text,
  start_time: start,
  end_time: end,
  words: [{ id: `${id}-w`, text, start_time: start, end_time: end }],
})

const vocalGaps: VocalGapsResult = {
  version: '0.2.0',
  gaps: [
    {
      start: 5,
      end: 11,
      duration: 6,
      active_fraction: 0.9,
      longest_run_s: 5.5,
      reference_lines: { genius: ['plain'] },
      synced_reference_lines: { lrclib: ['line one', 'line two words'] },
      suspect: true,
      evidenced: true,
    },
    {
      start: 20,
      end: 26,
      duration: 6,
      active_fraction: 0.8,
      longest_run_s: 4,
      reference_lines: {},
      synced_reference_lines: {},
      suspect: true,
      evidenced: false,
    },
  ],
}

const baseData = {
  corrected_segments: [seg('s1', 0, 5, 'intro'), seg('s2', 11, 20, 'verse'), seg('s3', 26, 30, 'outro')],
} as unknown as CorrectionData

/**
 * Mirrors LyricsAnalyzer's history machinery (history array + index, push on update,
 * undo = step the index back) so the test proves insertion is one undoable step.
 */
function useHarness(isReadOnly = false) {
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
  const missing = useMissingLyrics({ vocalGaps, data, updateDataWithHistory, editLog, isReadOnly })
  return { missing, data, undo: () => setIndex((i) => Math.max(0, i - 1)), historyLength: history.length, editLog }
}

describe('useMissingLyrics', () => {
  it('exposes only evidenced open gaps, with a marker at the chronological position', () => {
    const { result } = renderHook(() => useHarness())
    expect(result.current.missing.openGaps).toHaveLength(1)
    expect(result.current.missing.openGaps[0].lines).toEqual(['line one', 'line two words'])
    expect(result.current.missing.markers).toEqual([
      { id: 'missing-lyrics-5.00-11.00', beforeSegmentIndex: 1, start: 5, end: 11 },
    ])
  })

  it('inserts through the history (one undoable step) and the marker disappears / returns on undo', () => {
    const { result } = renderHook(() => useHarness())
    let ret: ReturnType<typeof result.current.missing.insertLines> = null
    act(() => {
      ret = result.current.missing.insertLines(result.current.missing.openGaps[0])
    })
    expect(ret).not.toBeNull()
    expect(ret!.insertedAt).toBe(1)
    expect(result.current.historyLength).toBe(2)
    expect(result.current.data.corrected_segments.map((s) => s.text)).toEqual([
      'intro',
      'line one',
      'line two words',
      'verse',
      'outro',
    ])
    expect(result.current.missing.openGaps).toEqual([])
    expect(result.current.missing.markers).toEqual([])

    // edit log: one segment_add per inserted line, tagged with the gap origin
    const adds = result.current.editLog.entries.filter((e) => e.operation === 'segment_add')
    expect(adds).toHaveLength(2)
    expect(adds[0].details).toMatchObject({ origin: 'missing_lyrics_gap', reference_source: 'lrclib', synced_reference: true })

    act(() => result.current.undo())
    expect(result.current.data.corrected_segments).toHaveLength(3)
    expect(result.current.missing.openGaps).toHaveLength(1)
  })

  it('does not insert in read-only mode', () => {
    const { result } = renderHook(() => useHarness(true))
    let ret: unknown = 'unset'
    act(() => {
      ret = result.current.missing.insertLines(result.current.missing.openGaps[0])
    })
    expect(ret).toBeNull()
    expect(result.current.historyLength).toBe(1)
    expect(result.current.missing.openGaps).toHaveLength(1)
  })
})
