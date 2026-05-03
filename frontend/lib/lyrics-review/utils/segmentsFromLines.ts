import type { LyricsSegment } from '@/lib/lyrics-review/types'
import { createWordsWithDistributedTiming } from '@/lib/lyrics-review/utils/wordUtils'

/**
 * Build an updated LyricsSegment[] by replacing each existing segment's text
 * with the corresponding line from `lines`. Word-level timing is preserved
 * when the new word count matches the old; otherwise distributed across
 * the original segment time range.
 *
 * Used by both the "Replace Segment Lyrics" mode and the
 * "Custom Lyrics" mode (where lines come from Gemini instead of paste).
 *
 * When `options.redistributedTiming` is provided, the function iterates over
 * `lines` instead of `existingSegments`, using the supplied (start, end) pairs
 * for each new segment. This supports variable-line-count AI responses.
 */
export interface SegmentsFromLinesOptions {
  /** Per-line (start, end) timing pairs supplied by the backend. When present,
   *  iteration is driven by `lines` (not `existingSegments`) and each segment
   *  receives the corresponding timing instead of inheriting from the original. */
  redistributedTiming?: Array<[number, number]>
}

export function segmentsFromLines(
  lines: string[],
  existingSegments: LyricsSegment[],
  options: SegmentsFromLinesOptions = {},
): LyricsSegment[] {
  const { redistributedTiming } = options

  // Variable-line-count path: iterate lines, use provided timing.
  if (redistributedTiming) {
    return lines.map((line, i) => {
      const newLineText = line.trim()
      const [start, end] = redistributedTiming[i] ?? [0, 0]

      const newWords = createWordsWithDistributedTiming(newLineText, start, end)
      return {
        id: existingSegments[i]?.id ?? `seg-new-${i}`,
        text: newLineText,
        start_time: start,
        end_time: end,
        words: newWords,
      }
    })
  }

  // Default path (backward compat): iterate existingSegments.
  return existingSegments.map((segment, i) => {
    const newLineText = (lines[i] ?? '').trim()
    const originalText = segment.text.trim()

    if (newLineText === originalText) {
      return JSON.parse(JSON.stringify(segment)) as LyricsSegment
    }

    const newWordTexts = newLineText.split(/\s+/).filter((w) => w.length > 0)

    if (newWordTexts.length === segment.words.length && newWordTexts.length > 0) {
      const updatedWords = segment.words.map((word, idx) => ({
        ...word,
        text: newWordTexts[idx],
      }))
      return {
        ...segment,
        text: newLineText,
        words: updatedWords,
      }
    }

    const newWords = createWordsWithDistributedTiming(
      newLineText,
      segment.start_time,
      segment.end_time,
    )

    return {
      ...segment,
      text: newLineText,
      words: newWords,
    }
  })
}
