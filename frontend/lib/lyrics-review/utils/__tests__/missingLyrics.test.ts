import {
  buildMissingLyricsSegments,
  findOpenMissingLyricsGaps,
  fitTimedLinesToWindow,
  formatGapTime,
  gapHasWords,
  gapInsertIndex,
  insertMissingLyrics,
  isGapConsistentWithSegments,
  pendingUntimedSegmentIndex,
  pickReferenceLines,
  planGapInsertion,
} from '../missingLyrics'
import { countUntimedWords } from '../timingCompleteness'
import type { CorrectionData, LyricsSegment, VocalGap, VocalGapsResult, Word } from '../../types'

const seg = (id: string, start: number, end: number, texts: string[] = ['a'], singer?: 0 | 1 | 2): LyricsSegment => {
  const step = (end - start) / texts.length
  const words = texts.map((text, i) => ({
    id: `${id}-w${i}`,
    text,
    start_time: start + i * step,
    end_time: start + (i + 1) * step,
  }))
  return { id, text: texts.join(' '), words, start_time: start, end_time: end, ...(singer !== undefined ? { singer } : {}) }
}

const untimedSeg = (id: string, texts: string[]): LyricsSegment => ({
  id,
  text: texts.join(' '),
  start_time: null,
  end_time: null,
  words: texts.map((text, i) => ({ id: `${id}-w${i}`, text, start_time: null, end_time: null })),
})

const gap = (overrides: Partial<VocalGap> = {}): VocalGap => ({
  start: 20.66,
  end: 32.4,
  duration: 11.74,
  active_fraction: 0.9,
  longest_run_s: 11.76,
  reference_lines: { genius: ['plain one', 'plain two'] },
  synced_reference_lines: { lrclib: ['synced one two', 'synced three'] },
  suspect: true,
  evidenced: true,
  ...overrides,
})

const result = (gaps: VocalGap[]): VocalGapsResult => ({ version: '0.2.0', gaps })

const segments = [seg('s1', 15, 20.66, ['before', 'gap']), seg('s2', 32.4, 36, ['after', 'gap'])]

const dataWith = (segs: LyricsSegment[], reference_lyrics: Record<string, unknown> = {}) =>
  ({ corrected_segments: segs, reference_lyrics }) as unknown as CorrectionData

describe('findOpenMissingLyricsGaps', () => {
  it('returns an evidenced gap with no words in its range', () => {
    const open = findOpenMissingLyricsGaps(result([gap()]), segments)
    expect(open).toHaveLength(1)
    expect(open[0].id).toBe('missing-lyrics-20.66-32.40')
    expect(open[0].pendingSegmentIndex).toBeNull()
  })

  it('hides the gap once a timed word starts inside it', () => {
    const filled = [...segments, seg('new', 25, 26, ['added'])]
    expect(findOpenMissingLyricsGaps(result([gap()]), filled, segments)).toEqual([])
  })

  it('treats words within the 2dp rounding tolerance of the edges as neighbours', () => {
    // Backend rounds: the next word really starts at 19.996 but gap.end is 20.0
    const g = gap({ start: 10, end: 20 })
    const segs = [seg('a', 8, 10.03, ['x']), seg('b', 19.996, 21, ['y'])]
    expect(gapHasWords(g, segs)).toBe(false)
    expect(findOpenMissingLyricsGaps(result([g]), segs)).toHaveLength(1)
    // ...but a word clearly inside still closes it
    expect(gapHasWords(g, [...segs, seg('c', 19.9, 19.95, ['z'])])).toBe(true)
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
    const segs = [...segments, seg('s3', 60, 62, ['end'])]
    const open = findOpenMissingLyricsGaps(result([gap({ start: 36, end: 60 }), gap()]), segs)
    expect(open.map((o) => o.gap.start)).toEqual([20.66, 36])
  })

  it('marks a gap pending (not open-for-insert) when untimed words sit at its position', () => {
    const segs = [segments[0], untimedSeg('u', ['typed', 'lyrics']), segments[1]]
    const open = findOpenMissingLyricsGaps(result([gap()]), segs, segments)
    expect(open).toHaveLength(1)
    expect(open[0].pendingSegmentIndex).toBe(1)
  })

  it('does not count untimed words elsewhere in the song as pending', () => {
    const segs = [untimedSeg('u', ['early']), ...segments, seg('s3', 40, 41), untimedSeg('v', ['late'])]
    expect(pendingUntimedSegmentIndex(gap(), segs)).toBeNull()
  })
})

describe('stale-analysis guard (isGapConsistentWithSegments)', () => {
  it('accepts a gap bracketed by words at load', () => {
    expect(isGapConsistentWithSegments(gap(), segments)).toBe(true)
  })

  it('rejects a gap whose neighbouring words are far away (analysis from another transcription)', () => {
    const shifted = [seg('s1', 10, 15, ['a']), seg('s2', 35, 36, ['b'])]
    expect(isGapConsistentWithSegments(gap(), shifted)).toBe(false)
    expect(findOpenMissingLyricsGaps(result([gap()]), shifted)).toEqual([])
  })

  it('rejects a gap that had words inside it at load', () => {
    expect(isGapConsistentWithSegments(gap(), [...segments, seg('m', 25, 26)])).toBe(false)
  })

  it('accepts a gap touching the song start or with no words after it', () => {
    expect(isGapConsistentWithSegments(gap({ start: 0, end: 15 }), segments)).toBe(true)
    expect(isGapConsistentWithSegments(gap({ start: 36, end: 50 }), segments)).toBe(true)
    expect(isGapConsistentWithSegments(gap({ start: 5, end: 15 }), segments)).toBe(false)
  })

  it('uses the INITIAL segments, so a gap stays trusted after the reviewer edits', () => {
    // current segments had the neighbour deleted; initial still brackets it
    const current = [segments[1]]
    expect(findOpenMissingLyricsGaps(result([gap()]), current, segments)).toHaveLength(1)
  })
})

describe('pickReferenceLines', () => {
  it('prefers synced reference lines', () => {
    expect(pickReferenceLines(gap())).toEqual({ source: 'lrclib', lines: ['synced one two', 'synced three'], synced: true })
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
    expect(pickReferenceLines(gap({ synced_reference_lines: {}, reference_lines: { g: ['  ', ''] } }))).toEqual({
      source: null,
      lines: [],
      synced: false,
    })
  })
})

describe('buildMissingLyricsSegments', () => {
  const refSeg = (id: string, text: string, start: number, timed = true): LyricsSegment => {
    const ws = text.split(' ')
    const words: Word[] = ws.map((t, i) => ({
      id: `${id}-${i}`,
      text: t,
      start_time: timed || i === 0 ? start + i * 0.5 : null,
      end_time: timed || i === 0 ? start + i * 0.5 + 0.4 : null,
    }))
    return { id, text, words, start_time: start, end_time: start + ws.length * 0.5 }
  }

  it('inserts words UNTIMED (no fabricated timing) when there is no synced reference', () => {
    const out = buildMissingLyricsSegments(gap(), ['one  two', 'three'])
    expect(out.map((s) => s.words.map((w) => w.text))).toEqual([['one', 'two'], ['three']])
    out.flatMap((s) => s.words).forEach((w) => {
      expect(w.start_time).toBeNull()
      expect(w.end_time).toBeNull()
    })
    // segment bounds = the gap, so Play / the Edit modal open on the right stretch
    expect(out[0].start_time).toBe(20.66)
    expect(out[0].end_time).toBe(32.4)
  })

  it('the existing submit guard catches untimed inserted words', () => {
    const out = buildMissingLyricsSegments(gap(), ['one two', 'three'])
    expect(countUntimedWords([...segments, ...out])).toBe(3)
  })

  it('uses the synced reference word timings when the line matches inside the gap', () => {
    const refs = [refSeg('r0', 'Synced one two', 5), refSeg('r1', 'synced one two', 22), refSeg('r2', 'synced three', 26)]
    const out = buildMissingLyricsSegments(gap(), ['synced one two', 'synced three'], undefined, refs)
    expect(out[0].words.map((w) => [w.text, w.start_time, w.end_time])).toEqual([
      ['synced', 22, 22.4],
      ['one', 22.5, 22.9],
      ['two', 23, 23.4],
    ])
    expect(out[0].start_time).toBe(22)
    expect(out[0].end_time).toBe(23.4)
    expect(out[1].words[0].start_time).toBe(26)
    expect(countUntimedWords(out)).toBe(0)
    // fresh ids, not the reference word ids
    expect(out[0].words[0].id).not.toBe('r1-0')
  })

  it('falls back to untimed for a line whose reference words are not all timed', () => {
    const refs = [refSeg('r1', 'synced one two', 22, false)]
    const out = buildMissingLyricsSegments(gap(), ['synced one two'], undefined, refs)
    expect(out[0].words.every((w) => w.start_time === null)).toBe(true)
  })

  it('uses each reference line once for a repeated line', () => {
    const refs = [refSeg('r1', 'la la', 22), refSeg('r2', 'la la', 25)]
    const out = buildMissingLyricsSegments(gap(), ['la la', 'la la'], undefined, refs)
    expect(out.map((s) => s.start_time)).toEqual([22, 25])
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

describe('insertion position', () => {
  it('inserts between the segments either side of the gap', () => {
    const data = dataWith(segments)
    expect(gapInsertIndex(gap(), segments)).toBe(1)
    const out = insertMissingLyrics(data, { gap: gap(), lines: ['x y', 'z'], source: 'genius', synced: false })
    expect(out.insertedAt).toBe(1)
    expect(out.split).toBeNull()
    expect(out.data.corrected_segments.map((s) => s.text)).toEqual(['before gap', 'x y', 'z', 'after gap'])
    expect(data.corrected_segments).toHaveLength(2) // pure
  })

  it('handles a gap before the first segment and after the last one', () => {
    expect(gapInsertIndex(gap({ start: 0, end: 15 }), segments)).toBe(0)
    expect(gapInsertIndex(gap({ start: 36, end: 50 }), segments)).toBe(2)
  })

  it('splits a segment the gap sits inside (gap between two of its words)', () => {
    // segment A: words 5–9s plus a trailing word at 22s → gap [9, 22)
    const a: LyricsSegment = {
      id: 'A',
      text: 'one two three',
      start_time: 5,
      end_time: 23,
      singer: 1,
      words: [
        { id: 'w1', text: 'one', start_time: 5, end_time: 7 },
        { id: 'w2', text: 'two', start_time: 7, end_time: 9 },
        { id: 'w3', text: 'three', start_time: 22, end_time: 23 },
      ],
    }
    const segs = [seg('pre', 0, 4), a, seg('post', 25, 26)]
    const g = gap({ start: 9, end: 22 })
    expect(gapInsertIndex(g, segs)).toBe(2)

    const plan = planGapInsertion(g, segs)
    expect(plan.split).toEqual({ index: 1, newSegmentId: expect.any(String) })

    const out = insertMissingLyrics(dataWith(segs), { gap: g, lines: ['dropped line'], source: 'genius', synced: false })
    const texts = out.data.corrected_segments.map((s) => s.text)
    expect(texts).toEqual(['a', 'one two', 'dropped line', 'three', 'a'])
    const [, first, , second] = out.data.corrected_segments
    expect(first.id).toBe('A')
    expect([first.start_time, first.end_time]).toEqual([5, 9])
    expect(second.id).toBe(out.split!.newSegmentId)
    expect(second.id).not.toBe('A')
    expect([second.start_time, second.end_time]).toEqual([22, 23])
    expect(second.words.map((w) => w.id)).toEqual(['w3']) // word ids preserved
    expect(second.singer).toBe(1)
    expect(out.data.corrected_segments[2].singer).toBe(1)
  })

  it('duet: inherits the FOLLOWING segment singer, then the preceding (like addSegmentBefore)', () => {
    const segs = [seg('s1', 15, 20.66, ['a'], 1), seg('s2', 32.4, 36, ['b'], 2)]
    const out = insertMissingLyrics(dataWith(segs), { gap: gap(), lines: ['x'], source: 'g', synced: false })
    expect(out.inserted[0].singer).toBe(2)
    // gap after the last segment → falls back to the preceding singer
    const tail = insertMissingLyrics(dataWith(segs), { gap: gap({ start: 36, end: 50 }), lines: ['x'], source: 'g', synced: false })
    expect(tail.inserted[0].singer).toBe(2)
    const segs2 = [seg('s1', 15, 20.66, ['a'], 1)]
    const tail2 = insertMissingLyrics(dataWith(segs2), { gap: gap({ start: 20.66, end: 30 }), lines: ['x'], source: 'g', synced: false })
    expect(tail2.inserted[0].singer).toBe(1)
  })

  it('looks up synced timings from data.reference_lyrics[source]', () => {
    const ref = {
      lrclib: {
        segments: [
          {
            id: 'r',
            text: 'synced three',
            start_time: 26,
            end_time: 27,
            words: [
              { id: 'r0', text: 'synced', start_time: 26, end_time: 26.5 },
              { id: 'r1', text: 'three', start_time: 26.5, end_time: 27 },
            ],
          },
        ],
      },
    }
    const out = insertMissingLyrics(dataWith(segments, ref), {
      gap: gap(),
      lines: ['synced one two', 'synced three'],
      source: 'lrclib',
      synced: true,
    })
    expect(out.inserted[0].words.every((w) => w.start_time === null)).toBe(true) // no ref match
    expect(out.inserted[1].words.map((w) => w.start_time)).toEqual([26, 26.5])
  })

  it('after inserting untimed lines the gap becomes pending (no second Insert)', () => {
    const out = insertMissingLyrics(dataWith(segments), { gap: gap(), lines: ['x y'], source: 'g', synced: false })
    const open = findOpenMissingLyricsGaps(result([gap()]), out.data.corrected_segments, segments)
    expect(open).toHaveLength(1)
    expect(open[0].pendingSegmentIndex).toBe(1)
  })
})

describe('formatGapTime', () => {
  it('formats as m:ss, flooring seconds', () => {
    expect(formatGapTime(20.66)).toBe('0:20')
    expect(formatGapTime(32.4)).toBe('0:32')
    expect(formatGapTime(125.9)).toBe('2:05')
  })
})


describe('fitting synced-reference timings into the gap (job 5710831e regression)', () => {
  // LRCLIB is line-synced; its last line in the gap ended at 33.07s while the next
  // transcribed line starts at 32.40s.
  const lrclibLine = (id: string, start: number, end: number, texts: string[]) => seg(id, start, end, texts)
  const reference = {
    lrclib: {
      segments: [
        lrclibLine('r1', 21.28, 24.97, ['line', 'one']),
        lrclibLine('r2', 24.97, 29.36, ['line', 'two']),
        lrclibLine('r3', 29.36, 33.07, ['line', 'three']),
      ],
    },
  }
  const open = { gap: gap(), lines: ['line one', 'line two', 'line three'], source: 'lrclib', synced: true }

  it('inserted words never overlap the following line; lines that fit keep exact timing', () => {
    const { inserted, data } = insertMissingLyrics(dataWith(segments, reference), open)
    expect(inserted).toHaveLength(3)
    expect(inserted[0].words[0].start_time).toBeCloseTo(21.28)
    expect(inserted[1].words[0].start_time).toBeCloseTo(24.97)
    expect(inserted[1].words[1].end_time).toBeCloseTo(29.36)
    const last = inserted[2].words[inserted[2].words.length - 1]
    expect(last.end_time).toBeLessThanOrEqual(32.4 - 0.05 + 1e-9)
    expect(inserted[2].words[0].start_time).toBeCloseTo(29.36)
    // chronological, non-overlapping across the whole result
    const words = data.corrected_segments.flatMap((s) => s.words).filter((w) => w.start_time !== null)
    for (let i = 1; i < words.length; i++) {
      expect(words[i].start_time!).toBeGreaterThanOrEqual(words[i - 1].end_time! - 1e-9)
    }
    expect(countUntimedWords(data.corrected_segments)).toBe(0)
  })

  it('a line that starts before the previous line ends is pushed after it', () => {
    const fitted = fitTimedLinesToWindow([seg('x', 18, 22, ['a', 'b'])], 20.71, 32.35)
    expect(fitted[0].words[0].start_time).toBeCloseTo(20.71)
    expect(fitted[0].words[1].end_time).toBeCloseTo(22)
  })

  it('a line with no room left falls back to untimed (submit guard forces a sync)', () => {
    const fitted = fitTimedLinesToWindow([seg('x', 32.3, 34, ['a', 'b', 'c'])], 20.71, 32.35)
    expect(fitted[0].words.every((w) => w.start_time === null && w.end_time === null)).toBe(true)
    expect(countUntimedWords(fitted)).toBe(3)
  })

  it('a zero-length reference line inside the window becomes untimed, not "timed"', () => {
    const zero: LyricsSegment = {
      ...seg('z', 21, 21, ['a', 'b']),
      words: [
        { id: 'z-w0', text: 'a', start_time: 21, end_time: 21 },
        { id: 'z-w1', text: 'b', start_time: 21, end_time: 21 },
      ],
    }
    const fitted = fitTimedLinesToWindow([zero], 20.71, 32.35)
    expect(countUntimedWords(fitted)).toBe(2)
  })

  it('untimed lines pass through unchanged', () => {
    const u = untimedSeg('u', ['a', 'b'])
    expect(fitTimedLinesToWindow([u], 0, 10)[0]).toBe(u)
  })
})
