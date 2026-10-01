import {
  buildMissingLyricsSegments,
  findOpenMissingLyricsGaps,
  formatGapTime,
  gapHasWords,
  gapInsertIndex,
  insertMissingLyrics,
  pickReferenceLines,
  MISSING_LYRICS_EDGE_PAD_S,
} from '../missingLyrics'
import type { CorrectionData, LyricsSegment, VocalGap, VocalGapsResult } from '../../types'

const seg = (id: string, start: number, end: number, texts: string[] = ['a']): LyricsSegment => {
  const step = (end - start) / texts.length
  const words = texts.map((text, i) => ({
    id: `${id}-w${i}`,
    text,
    start_time: start + i * step,
    end_time: start + (i + 1) * step,
  }))
  return { id, text: texts.join(' '), words, start_time: start, end_time: end }
}

const gap = (overrides: Partial<VocalGap> = {}): VocalGap => ({
  start: 20.66,
  end: 32.4,
  duration: 11.74,
  active_fraction: 0.9,
  longest_run_s: 11.76,
  reference_lines: { genius: ['plain one', 'plain two'] },
  synced_reference_lines: { lrclib: ['synced one two', 'synced three', 'synced four five six'] },
  suspect: true,
  evidenced: true,
  ...overrides,
})

const result = (gaps: VocalGap[]): VocalGapsResult => ({
  version: '0.2.0',
  gaps,
  suspect_count: gaps.filter((g) => g.suspect).length,
  evidenced_count: gaps.filter((g) => g.evidenced).length,
})

const segments = [seg('s1', 15, 20.66, ['before', 'gap']), seg('s2', 32.4, 36, ['after', 'gap'])]

describe('findOpenMissingLyricsGaps', () => {
  it('returns an evidenced gap with no words in its range', () => {
    const open = findOpenMissingLyricsGaps(result([gap()]), segments)
    expect(open).toHaveLength(1)
    expect(open[0].gap.start).toBe(20.66)
    expect(open[0].id).toBe('missing-lyrics-20.66-32.40')
  })

  it('hides the gap once a word starts inside it', () => {
    const filled = [...segments, seg('new', 25, 26, ['added'])]
    expect(findOpenMissingLyricsGaps(result([gap()]), filled)).toEqual([])
  })

  it('ignores words that merely touch the gap edges', () => {
    // s1 ends exactly at gap.start and s2 starts exactly at gap.end
    expect(gapHasWords(gap(), segments)).toBe(false)
  })

  it('ignores untimed words', () => {
    const untimed: LyricsSegment = {
      id: 'u',
      text: 'x',
      start_time: null,
      end_time: null,
      words: [{ id: 'u1', text: 'x', start_time: null, end_time: null }],
    }
    expect(findOpenMissingLyricsGaps(result([gap()]), [...segments, untimed])).toHaveLength(1)
  })

  it('does not show non-evidenced (audio-only) suspect gaps', () => {
    expect(findOpenMissingLyricsGaps(result([gap({ evidenced: false })]), segments)).toEqual([])
  })

  it('returns nothing for null / empty / missing gaps', () => {
    expect(findOpenMissingLyricsGaps(null, segments)).toEqual([])
    expect(findOpenMissingLyricsGaps(undefined, segments)).toEqual([])
    expect(findOpenMissingLyricsGaps({ version: '0.2.0' }, segments)).toEqual([])
    expect(findOpenMissingLyricsGaps(result([]), segments)).toEqual([])
  })

  it('sorts open gaps chronologically', () => {
    const open = findOpenMissingLyricsGaps(
      result([gap({ start: 50, end: 60 }), gap()]),
      segments
    )
    expect(open.map((o) => o.gap.start)).toEqual([20.66, 50])
  })
})

describe('pickReferenceLines', () => {
  it('prefers synced reference lines', () => {
    expect(pickReferenceLines(gap())).toEqual({
      source: 'lrclib',
      lines: ['synced one two', 'synced three', 'synced four five six'],
      synced: true,
    })
  })

  it('falls back to reference_lines when synced is empty or missing', () => {
    expect(pickReferenceLines(gap({ synced_reference_lines: { lrclib: [] } }))).toEqual({
      source: 'genius',
      lines: ['plain one', 'plain two'],
      synced: false,
    })
    expect(pickReferenceLines(gap({ synced_reference_lines: undefined })).source).toBe('genius')
  })

  it('drops blank lines and returns empty when nothing is left', () => {
    expect(
      pickReferenceLines(gap({ synced_reference_lines: {}, reference_lines: { g: ['  ', ''] } }))
    ).toEqual({ source: null, lines: [], synced: false })
  })
})

describe('buildMissingLyricsSegments', () => {
  it('makes one segment per line, words split on whitespace', () => {
    const out = buildMissingLyricsSegments(gap(), ['one  two', 'three'])
    expect(out.map((s) => s.words.map((w) => w.text))).toEqual([['one', 'two'], ['three']])
    expect(out.map((s) => s.text)).toEqual(['one two', 'three'])
  })

  it('spreads timings evenly across [start+pad, end-pad] in order', () => {
    const g = gap({ start: 10, end: 16.2 })
    const out = buildMissingLyricsSegments(g, ['a b', 'c', 'd e f'])
    const words = out.flatMap((s) => s.words)
    expect(words[0].start_time).toBeCloseTo(10 + MISSING_LYRICS_EDGE_PAD_S)
    expect(words[words.length - 1].end_time).toBeCloseTo(16.2 - MISSING_LYRICS_EDGE_PAD_S)
    const durations = words.map((w) => (w.end_time as number) - (w.start_time as number))
    durations.forEach((d) => expect(d).toBeCloseTo(1.0, 2))
    for (let i = 1; i < words.length; i++) {
      expect(words[i].start_time as number).toBeGreaterThanOrEqual(words[i - 1].end_time as number - 1e-9)
    }
    out.forEach((s) => {
      expect(s.start_time).toBe(s.words[0].start_time)
      expect(s.end_time).toBe(s.words[s.words.length - 1].end_time)
    })
  })

  it('gives every word and segment a unique id and carries the singer', () => {
    const out = buildMissingLyricsSegments(gap(), ['a b', 'c'], 2)
    const ids = [...out.map((s) => s.id), ...out.flatMap((s) => s.words.map((w) => w.id))]
    expect(new Set(ids).size).toBe(ids.length)
    out.forEach((s) => expect(s.singer).toBe(2))
  })

  it('returns no segments for blank lines', () => {
    expect(buildMissingLyricsSegments(gap(), ['   '])).toEqual([])
  })
})

describe('gapInsertIndex / insertMissingLyrics', () => {
  const data = { corrected_segments: segments } as unknown as CorrectionData

  it('inserts between the segments either side of the gap', () => {
    expect(gapInsertIndex(gap(), segments)).toBe(1)
    const { data: out, insertedAt, inserted } = insertMissingLyrics(data, gap(), ['x y', 'z'])
    expect(insertedAt).toBe(1)
    expect(inserted).toHaveLength(2)
    expect(out.corrected_segments.map((s) => s.text)).toEqual(['before gap', 'x y', 'z', 'after gap'])
    const starts = out.corrected_segments.map((s) => s.start_time as number)
    expect([...starts].sort((a, b) => a - b)).toEqual(starts)
    // pure: original data untouched
    expect(data.corrected_segments).toHaveLength(2)
  })

  it('handles a gap before the first segment and after the last one', () => {
    expect(gapInsertIndex(gap({ start: 0, end: 10 }), segments)).toBe(0)
    expect(gapInsertIndex(gap({ start: 40, end: 50 }), segments)).toBe(2)
  })

  it('makes the gap no longer open', () => {
    const { data: out } = insertMissingLyrics(data, gap(), ['x y'])
    expect(findOpenMissingLyricsGaps(result([gap()]), out.corrected_segments)).toEqual([])
  })
})

describe('formatGapTime', () => {
  it('formats as m:ss, flooring seconds', () => {
    expect(formatGapTime(20.66)).toBe('0:20')
    expect(formatGapTime(32.4)).toBe('0:32')
    expect(formatGapTime(125.9)).toBe('2:05')
  })
})
