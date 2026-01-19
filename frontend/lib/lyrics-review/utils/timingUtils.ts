import { LyricsSegment, Word, CorrectionData } from '../types'

/**
 * Apply the timing offset to a single timestamp
 * @param time The original timestamp in seconds
 * @param offsetMs The offset in milliseconds
 * @returns The adjusted timestamp in seconds
 */
export const applyOffsetToTime = (time: number | null, offsetMs: number): number | null => {
  if (time === null) return null
  // Convert ms to seconds and add to the time
  return time + offsetMs / 1000
}

/**
 * Apply the timing offset to a word
 * @param word The original word object
 * @param offsetMs The offset in milliseconds
 * @returns A new word object with adjusted timestamps
 */
export const applyOffsetToWord = (word: Word, offsetMs: number): Word => {
  if (offsetMs === 0) return word

  return {
    ...word,
    start_time: applyOffsetToTime(word.start_time, offsetMs),
    end_time: applyOffsetToTime(word.end_time, offsetMs),
  }
}

/**
 * Apply the timing offset to all words in a segment
 * Also updates the segment's start and end times based on the first and last word
 * @param segment The original segment
 * @param offsetMs The offset in milliseconds
 * @returns A new segment with adjusted timestamps
 */
export const applyOffsetToSegment = (segment: LyricsSegment, offsetMs: number): LyricsSegment => {
  if (offsetMs === 0) return segment

  const adjustedWords = segment.words.map((word) => applyOffsetToWord(word, offsetMs))

  // Update segment start/end times based on first/last word
  const validStartTimes = adjustedWords
    .map((w) => w.start_time)
    .filter((t): t is number => t !== null)
  const validEndTimes = adjustedWords.map((w) => w.end_time).filter((t): t is number => t !== null)

  const segmentStartTime = validStartTimes.length > 0 ? Math.min(...validStartTimes) : null
  const segmentEndTime = validEndTimes.length > 0 ? Math.max(...validEndTimes) : null

  return {
    ...segment,
    words: adjustedWords,
    start_time: segmentStartTime,
    end_time: segmentEndTime,
  }
}

/**
 * Apply the timing offset to the entire correction data
 * This creates a new data object with all timestamps adjusted by the offset
 * @param data The original correction data
 * @param offsetMs The offset in milliseconds
 * @returns A new correction data object with all timestamps adjusted
 */
export const applyOffsetToCorrectionData = (
  data: CorrectionData,
  offsetMs: number
): CorrectionData => {
  if (offsetMs === 0) {
    return data
  }

  return {
    ...data,
    corrected_segments: data.corrected_segments.map((segment) =>
      applyOffsetToSegment(segment, offsetMs)
    ),
  }
}
