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
 */
export function segmentsFromLines(
  lines: string[],
  existingSegments: LyricsSegment[],
): LyricsSegment[] {
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
