import { nanoid } from 'nanoid'
import { CorrectionData, LyricsSegment } from '../types'
import { resolveSegmentSinger } from '../duet'

export const addSegmentBefore = (data: CorrectionData, beforeIndex: number): CorrectionData => {
  const newData = { ...data }
  const beforeSegment = newData.corrected_segments[beforeIndex]

  // Create new segment starting 1 second before the target segment
  // Use 0 as default if start_time is null
  const newStartTime = Math.max(0, (beforeSegment.start_time ?? 1) - 1)
  const newEndTime = newStartTime + 1

  const newSegment: LyricsSegment = {
    id: nanoid(),
    text: 'REPLACE',
    start_time: newStartTime,
    end_time: newEndTime,
    ...(beforeSegment.singer !== undefined ? { singer: beforeSegment.singer } : {}),
    words: [
      {
        id: nanoid(),
        text: 'REPLACE',
        start_time: newStartTime,
        end_time: newEndTime,
        confidence: 1.0,
      },
    ],
  }

  // Insert the new segment before the current one
  newData.corrected_segments.splice(beforeIndex, 0, newSegment)

  return newData
}

export const splitSegment = (
  data: CorrectionData,
  segmentIndex: number,
  afterWordIndex: number
): CorrectionData | null => {
  const newData = { ...data }
  const segment = newData.corrected_segments[segmentIndex]

  // Split the words array
  const firstHalfWords = segment.words.slice(0, afterWordIndex + 1)
  const secondHalfWords = segment.words.slice(afterWordIndex + 1)

  if (secondHalfWords.length === 0) return null // Nothing to split

  const lastFirstWord = firstHalfWords[firstHalfWords.length - 1]
  const firstSecondWord = secondHalfWords[0]
  const lastSecondWord = secondHalfWords[secondHalfWords.length - 1]

  // Create two segments from the split
  const firstSegment: LyricsSegment = {
    ...segment,
    words: firstHalfWords,
    text: firstHalfWords.map((w) => w.text).join(' '),
    end_time: lastFirstWord.end_time ?? null,
  }

  const secondSegment: LyricsSegment = {
    id: nanoid(),
    words: secondHalfWords,
    text: secondHalfWords.map((w) => w.text).join(' '),
    start_time: firstSecondWord.start_time ?? null,
    end_time: lastSecondWord.end_time ?? null,
    ...(segment.singer !== undefined ? { singer: segment.singer } : {}),
  }

  // Replace the original segment with the two new segments
  newData.corrected_segments.splice(segmentIndex, 1, firstSegment, secondSegment)

  return newData
}

export const deleteSegment = (data: CorrectionData, segmentIndex: number): CorrectionData => {
  const newData = { ...data }
  const deletedSegment = newData.corrected_segments[segmentIndex]

  // Remove segment
  newData.corrected_segments = newData.corrected_segments.filter((_, index) => index !== segmentIndex)

  // Update anchor sequences to remove references to deleted words
  newData.anchor_sequences = newData.anchor_sequences.map((anchor) => ({
    ...anchor,
    transcribed_word_ids: anchor.transcribed_word_ids.filter(
      (wordId) => !deletedSegment.words.some((deletedWord) => deletedWord.id === wordId)
    ),
  }))

  // Update gap sequences to remove references to deleted words
  newData.gap_sequences = newData.gap_sequences.map((gap) => ({
    ...gap,
    transcribed_word_ids: gap.transcribed_word_ids.filter(
      (wordId) => !deletedSegment.words.some((deletedWord) => deletedWord.id === wordId)
    ),
  }))

  return newData
}

export const updateSegment = (
  data: CorrectionData,
  segmentIndex: number,
  updatedSegment: LyricsSegment
): CorrectionData => {
  const newData = { ...data }

  // Ensure new words have IDs
  updatedSegment.words = updatedSegment.words.map((word) => ({
    ...word,
    id: word.id || nanoid(),
  }))

  newData.corrected_segments[segmentIndex] = updatedSegment

  return newData
}

export function mergeSegment(
  data: CorrectionData,
  segmentIndex: number,
  mergeWithNext: boolean
): CorrectionData {
  const segments = [...data.corrected_segments]
  const targetIndex = mergeWithNext ? segmentIndex + 1 : segmentIndex - 1

  // Check if target segment exists
  if (targetIndex < 0 || targetIndex >= segments.length) {
    return data
  }

  const baseSegment = segments[segmentIndex]
  const targetSegment = segments[targetIndex]

  // For merge, "first segment" is whichever comes first in the output.
  // mergeWithNext: base is first, target is second.
  // mergeWithPrev: target is first, base is second.
  const firstSegment = mergeWithNext ? baseSegment : targetSegment
  const secondSegment = mergeWithNext ? targetSegment : baseSegment

  // Promote implicit word singers from the second segment into explicit overrides.
  // Words in secondSegment with no word-level singer inherit secondSegment's
  // RESOLVED singer (explicit value or the default of 1). After merge, the
  // merged segment takes firstSegment's resolved singer, so any second-segment
  // words whose resolved singer differs must be explicitly pinned to preserve
  // their singer assignment. Using `resolveSegmentSinger` covers the case
  // where secondSegment.singer is undefined (implicitly 1) while firstSegment
  // is explicitly 2 — without this, w2 would silently flip to singer 2 on merge.
  const firstResolved = resolveSegmentSinger(firstSegment)
  const secondResolved = resolveSegmentSinger(secondSegment)
  const promoteSecondWords = secondSegment.words.map((word) => {
    if (word.singer === undefined && firstResolved !== secondResolved) {
      return { ...word, singer: secondResolved }
    }
    return word
  })

  // Create merged segment
  const mergedSegment: LyricsSegment = {
    id: nanoid(),
    words: [...firstSegment.words, ...promoteSecondWords],
    text: `${firstSegment.text} ${secondSegment.text}`,
    start_time: Math.min(baseSegment.start_time ?? Infinity, targetSegment.start_time ?? Infinity),
    end_time: Math.max(baseSegment.end_time ?? -Infinity, targetSegment.end_time ?? -Infinity),
    ...(firstSegment.singer !== undefined ? { singer: firstSegment.singer } : {}),
  }

  // Replace the two segments with the merged one
  const minIndex = Math.min(segmentIndex, targetIndex)
  segments.splice(minIndex, 2, mergedSegment)

  return {
    ...data,
    corrected_segments: segments,
  }
}

export function findAndReplace(
  data: CorrectionData,
  findText: string,
  replaceText: string,
  options: { caseSensitive: boolean; useRegex: boolean; fullTextMode?: boolean } = {
    caseSensitive: false,
    useRegex: false,
    fullTextMode: false,
  }
): CorrectionData {
  const newData = { ...data }

  // If full text mode is enabled, perform replacements across word boundaries
  if (options.fullTextMode) {
    newData.corrected_segments = data.corrected_segments.map((segment) => {
      // Create a pattern for the full segment text
      let pattern: RegExp

      if (options.useRegex) {
        pattern = new RegExp(findText, options.caseSensitive ? 'g' : 'gi')
      } else {
        const escapedFindText = findText.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
        pattern = new RegExp(escapedFindText, options.caseSensitive ? 'g' : 'gi')
      }

      // Get the full segment text
      const segmentText = segment.text

      // If no matches, return the segment unchanged
      if (!pattern.test(segmentText)) {
        return segment
      }

      // Reset pattern for replacement
      pattern.lastIndex = 0

      // Replace in the full segment text
      const newSegmentText = segmentText.replace(pattern, replaceText)

      // Split the new text into words
      const newWordTexts = newSegmentText
        .trim()
        .split(/\s+/)
        .filter((text) => text.length > 0)

      // Create new word objects
      // We'll try to preserve original word IDs and timing info where possible
      const newWords = []

      // If we have the same number of words, we can preserve IDs and timing
      if (newWordTexts.length === segment.words.length) {
        for (let i = 0; i < newWordTexts.length; i++) {
          newWords.push({
            ...segment.words[i],
            text: newWordTexts[i],
          })
        }
      }
      // If we have fewer words than before, some words were removed
      else if (newWordTexts.length < segment.words.length) {
        // Try to map new words to old words
        let oldWordIndex = 0
        for (let i = 0; i < newWordTexts.length; i++) {
          // Find the next non-empty old word
          while (
            oldWordIndex < segment.words.length &&
            segment.words[oldWordIndex].text.trim() === ''
          ) {
            oldWordIndex++
          }

          if (oldWordIndex < segment.words.length) {
            newWords.push({
              ...segment.words[oldWordIndex],
              text: newWordTexts[i],
            })
            oldWordIndex++
          } else {
            // If we run out of old words, create new ones
            newWords.push({
              id: nanoid(),
              text: newWordTexts[i],
              start_time: null,
              end_time: null,
            })
          }
        }
      }
      // If we have more words than before, some words were added
      else {
        // Try to preserve original words where possible
        for (let i = 0; i < newWordTexts.length; i++) {
          if (i < segment.words.length) {
            newWords.push({
              ...segment.words[i],
              text: newWordTexts[i],
            })
          } else {
            // For new words, create new IDs
            newWords.push({
              id: nanoid(),
              text: newWordTexts[i],
              start_time: null,
              end_time: null,
            })
          }
        }
      }

      return {
        ...segment,
        words: newWords,
        text: newSegmentText,
      }
    })
  }
  // Word-level replacement (original implementation)
  else {
    newData.corrected_segments = data.corrected_segments.map((segment) => {
      // Replace in each word
      let newWords = segment.words.map((word) => {
        let pattern: RegExp

        if (options.useRegex) {
          // Create regex with or without case sensitivity
          pattern = new RegExp(findText, options.caseSensitive ? 'g' : 'gi')
        } else {
          // Escape special regex characters for literal search
          const escapedFindText = findText.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
          pattern = new RegExp(escapedFindText, options.caseSensitive ? 'g' : 'gi')
        }

        return {
          ...word,
          text: word.text.replace(pattern, replaceText),
        }
      })

      // Filter out words that have become empty
      newWords = newWords.filter((word) => word.text.trim() !== '')

      // Update segment text
      return {
        ...segment,
        words: newWords,
        text: newWords.map((w) => w.text).join(' '),
      }
    })
  }

  // Filter out segments that have no words left
  newData.corrected_segments = newData.corrected_segments.filter((segment) => segment.words.length > 0)

  return newData
}

/**
 * Deletes a word from a segment in the correction data
 * @param data The correction data
 * @param wordId The ID of the word to delete
 * @returns Updated correction data with the word removed
 */
export function deleteWord(data: CorrectionData, wordId: string): CorrectionData {
  // Find the segment containing this word
  const segmentIndex = data.corrected_segments.findIndex((segment) =>
    segment.words.some((word) => word.id === wordId)
  )

  if (segmentIndex === -1) {
    // Word not found, return data unchanged
    return data
  }

  const segment = data.corrected_segments[segmentIndex]
  const wordIndex = segment.words.findIndex((word) => word.id === wordId)

  if (wordIndex === -1) {
    // Word not found in segment (shouldn't happen), return data unchanged
    return data
  }

  // Create a new segment with the word removed
  const updatedWords = segment.words.filter((_, index) => index !== wordIndex)

  if (updatedWords.length > 0) {
    // Update the segment with the word removed
    const updatedSegment = {
      ...segment,
      words: updatedWords,
      text: updatedWords.map((w) => w.text).join(' '),
    }

    // Update the data
    return updateSegment(data, segmentIndex, updatedSegment)
  } else {
    // If the segment would be empty, delete the entire segment
    return deleteSegment(data, segmentIndex)
  }
}
