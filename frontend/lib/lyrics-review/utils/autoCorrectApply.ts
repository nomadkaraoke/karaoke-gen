/**
 * Pure apply/revert logic for AI auto-correct suggestions.
 *
 * Suggestions reference words by id, so they stay valid as long as the
 * targeted words still exist — if the reviewer has edited those words since
 * the suggestions were generated, the suggestion is "stale" and cannot be
 * applied. Every apply returns enough information to undo that single
 * suggestion later, independent of the global history stack.
 */
import { nanoid } from 'nanoid'
import type { LyricsSegment, Word } from '../types'
import type { AiSuggestion } from '@/lib/api/autoCorrect'

export interface SuggestionUndoInfo {
  suggestionId: string
  op: AiSuggestion['op']
  /** Word ids created by the apply (replace / insert_after). */
  newWordIds: string[]
  /** Original words removed by the apply (replace / delete). */
  removedWords: Word[]
  /** Segment the change happened in. */
  segmentId: string
  /** Id of the word preceding the affected span (null = segment start). */
  prevWordId: string | null
  /** Set when a delete emptied the segment and removed it entirely. */
  removedSegment: { segment: LyricsSegment; index: number } | null
}

function rebuildText(words: Word[]): string {
  return words.map((w) => w.text).join(' ')
}

function findSegmentWithWords(
  segments: LyricsSegment[],
  wordIds: string[],
): { segment: LyricsSegment; index: number; wordIndices: number[] } | null {
  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i]
    const indices = wordIds.map((id) => seg.words.findIndex((w) => w.id === id))
    if (indices.every((x) => x >= 0)) {
      return { segment: seg, index: i, wordIndices: indices }
    }
  }
  return null
}

/** A suggestion is stale when its target words no longer exist in one segment, contiguously. */
export function isSuggestionStale(
  segments: LyricsSegment[],
  suggestion: AiSuggestion,
): boolean {
  const found = findSegmentWithWords(segments, suggestion.word_ids)
  if (!found) return true
  const sorted = [...found.wordIndices].sort((a, b) => a - b)
  for (let i = 1; i < sorted.length; i++) {
    if (sorted[i] !== sorted[i - 1] + 1) return true
  }
  return false
}

/** Evenly distribute [start, end] across n new words; null timings pass through. */
function distributeTimings(
  n: number,
  start: number | null,
  end: number | null,
): Array<{ start_time: number | null; end_time: number | null }> {
  if (n <= 0) return []
  if (start === null || end === null || end <= start) {
    return Array.from({ length: n }, () => ({
      start_time: start,
      end_time: end,
    }))
  }
  const step = (end - start) / n
  return Array.from({ length: n }, (_, i) => ({
    start_time: start + i * step,
    end_time: start + (i + 1) * step,
  }))
}

export function applySuggestion(
  segments: LyricsSegment[],
  suggestion: AiSuggestion,
): { segments: LyricsSegment[]; undo: SuggestionUndoInfo } | null {
  if (isSuggestionStale(segments, suggestion)) return null
  const found = findSegmentWithWords(segments, suggestion.word_ids)
  if (!found) return null
  const { segment, index: segIndex, wordIndices } = found
  const spanStart = Math.min(...wordIndices)
  const spanEnd = Math.max(...wordIndices)
  const prevWordId = spanStart > 0 ? segment.words[spanStart - 1].id : null

  const result = segments.map((s) => ({ ...s, words: [...s.words] }))
  const seg = result[segIndex]

  if (suggestion.op === 'insert_after') {
    const anchor = seg.words[spanEnd]
    const next = seg.words[spanEnd + 1] as Word | undefined
    const newTexts = suggestion.new_text.split(/\s+/).filter(Boolean)
    const windowStart = anchor.end_time
    const windowEnd =
      next?.start_time != null && windowStart != null && next.start_time > windowStart
        ? next.start_time
        : windowStart != null
          ? windowStart + 0.5 * newTexts.length
          : null
    const timings = distributeTimings(newTexts.length, windowStart, windowEnd)
    const newWords: Word[] = newTexts.map((text, i) => ({
      id: nanoid(),
      text,
      start_time: timings[i].start_time,
      end_time: timings[i].end_time,
      confidence: 1,
      created_during_correction: true,
    }))
    seg.words.splice(spanEnd + 1, 0, ...newWords)
    seg.text = rebuildText(seg.words)
    return {
      segments: result,
      undo: {
        suggestionId: suggestion.id,
        op: suggestion.op,
        newWordIds: newWords.map((w) => w.id),
        removedWords: [],
        segmentId: seg.id,
        prevWordId: anchor.id,
        removedSegment: null,
      },
    }
  }

  const removedWords = seg.words.slice(spanStart, spanEnd + 1)

  if (suggestion.op === 'delete') {
    seg.words.splice(spanStart, removedWords.length)
    let removedSegment: SuggestionUndoInfo['removedSegment'] = null
    if (seg.words.length === 0) {
      removedSegment = { segment: segments[segIndex], index: segIndex }
      result.splice(segIndex, 1)
    } else {
      seg.text = rebuildText(seg.words)
      seg.start_time = seg.words[0].start_time
      seg.end_time = seg.words[seg.words.length - 1].end_time
    }
    return {
      segments: result,
      undo: {
        suggestionId: suggestion.id,
        op: suggestion.op,
        newWordIds: [],
        removedWords,
        segmentId: seg.id,
        prevWordId,
        removedSegment,
      },
    }
  }

  // replace
  const newTexts = suggestion.new_text.split(/\s+/).filter(Boolean)
  const timings = distributeTimings(
    newTexts.length,
    removedWords[0]?.start_time ?? null,
    removedWords[removedWords.length - 1]?.end_time ?? null,
  )
  const newWords: Word[] = newTexts.map((text, i) => ({
    id: nanoid(),
    text,
    start_time: timings[i].start_time,
    end_time: timings[i].end_time,
    confidence: 1,
    created_during_correction: true,
  }))
  seg.words.splice(spanStart, removedWords.length, ...newWords)
  seg.text = rebuildText(seg.words)
  return {
    segments: result,
    undo: {
      suggestionId: suggestion.id,
      op: suggestion.op,
      newWordIds: newWords.map((w) => w.id),
      removedWords,
      segmentId: seg.id,
      prevWordId,
      removedSegment: null,
    },
  }
}

/**
 * Undo a previously applied suggestion. Returns null when the affected
 * words/segment have since been edited in a way that makes a safe revert
 * impossible (caller should tell the user to use global Undo instead).
 */
export function revertSuggestion(
  segments: LyricsSegment[],
  undo: SuggestionUndoInfo,
): LyricsSegment[] | null {
  // Deleted-whole-segment case: reinsert the snapshot.
  if (undo.removedSegment) {
    const result = segments.map((s) => ({ ...s, words: [...s.words] }))
    const at = Math.min(undo.removedSegment.index, result.length)
    result.splice(at, 0, JSON.parse(JSON.stringify(undo.removedSegment.segment)))
    return result
  }

  const segIndex = segments.findIndex((s) => s.id === undo.segmentId)
  if (segIndex < 0) return null
  const result = segments.map((s) => ({ ...s, words: [...s.words] }))
  const seg = result[segIndex]

  if (undo.newWordIds.length > 0) {
    // replace / insert_after: the created words must still be present, contiguous.
    const indices = undo.newWordIds.map((id) =>
      seg.words.findIndex((w) => w.id === id),
    )
    if (indices.some((x) => x < 0)) return null
    const sorted = [...indices].sort((a, b) => a - b)
    for (let i = 1; i < sorted.length; i++) {
      if (sorted[i] !== sorted[i - 1] + 1) return null
    }
    seg.words.splice(
      sorted[0],
      sorted.length,
      ...JSON.parse(JSON.stringify(undo.removedWords)),
    )
  } else {
    // delete: reinsert removed words after prevWordId (or at segment start).
    let at = 0
    if (undo.prevWordId) {
      const prevIndex = seg.words.findIndex((w) => w.id === undo.prevWordId)
      if (prevIndex < 0) return null
      at = prevIndex + 1
    }
    seg.words.splice(at, 0, ...JSON.parse(JSON.stringify(undo.removedWords)))
  }

  seg.text = rebuildText(seg.words)
  if (seg.words.length > 0) {
    seg.start_time = seg.words[0].start_time
    seg.end_time = seg.words[seg.words.length - 1].end_time
  }
  return result
}
