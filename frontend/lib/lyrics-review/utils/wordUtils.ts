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
 * Convert an array of word IDs to their corresponding Word objects
 * Filters out any IDs that don't match to valid words
 */
export function getWordsFromIds(segments: LyricsSegment[], wordIds: string[]): Word[] {
  return wordIds.map((id) => findWordById(segments, id)).filter((word): word is Word => word !== undefined)
}
