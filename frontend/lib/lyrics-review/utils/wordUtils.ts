import { nanoid } from 'nanoid'
import { Word, LyricsSegment } from '../types'

/**
 * Find a Word object by its ID within an array of segments
 */
export function findWordById(segments: LyricsSegment[], wordId: string): Word | undefined {
  for (const segment of segments) {
    const word = segment.words.find((w) => w.id === wordId)
    if (word) return word
  }
  return undefined
}

/**
 * Split a word's text into multiple parts and distribute timing equally.
 *
 * @param text - The text to split (by whitespace or in half if single word)
 * @param startTime - Original word's start time (null if no timing)
 * @param endTime - Original word's end time (null if no timing)
 * @returns Array of objects with text and timing for each split word
 */
export function splitWordWithTiming(
  text: string,
  startTime: number | null,
  endTime: number | null
): Array<{ text: string; start_time: number | null; end_time: number | null }> {
  // Split by whitespace, filter empty strings
  let words = text.split(/\s+/).filter((w) => w.length > 0)

  // If single word, split in half (but not if empty after whitespace filtering)
  if (words.length === 1) {
    const word = words[0]
    const half = Math.ceil(word.length / 2)
    words = [word.slice(0, half), word.slice(half)].filter((w) => w.length > 0)
  }

  // Handle empty input - return empty array
  if (words.length === 0) {
    return []
  }

  // Calculate timing distribution
  const hasValidTiming = startTime !== null && endTime !== null
  const wordDuration = hasValidTiming ? (endTime - startTime) / words.length : 0

  return words.map((wordText, i) => ({
    text: wordText,
    start_time: hasValidTiming ? startTime + i * wordDuration : null,
    end_time: hasValidTiming ? startTime + (i + 1) * wordDuration : null,
  }))
}

/**
 * Create Word objects with evenly distributed timing across a segment's duration.
 * Used when replacing segment text while preserving the segment's time range.
 */
export function createWordsWithDistributedTiming(
  text: string,
  segStart: number | null,
  segEnd: number | null
): Word[] {
  const wordTexts = text.trim().split(/\s+/).filter((w) => w.length > 0)
  if (wordTexts.length === 0) return []

  const start = segStart ?? 0
  const end = segEnd ?? start + 1
  const totalDuration = end - start
  const wordDuration = totalDuration / wordTexts.length

  return wordTexts.map((text, index) => ({
    id: nanoid(),
    text,
    start_time: start + index * wordDuration,
    end_time: start + (index + 1) * wordDuration,
    confidence: 1.0,
  }))
}

/**
 * Convert an array of word IDs to their corresponding Word objects
 * Filters out any IDs that don't match to valid words
 */
export function getWordsFromIds(segments: LyricsSegment[], wordIds: string[]): Word[] {
  return wordIds.map((id) => findWordById(segments, id)).filter((word): word is Word => word !== undefined)
}
