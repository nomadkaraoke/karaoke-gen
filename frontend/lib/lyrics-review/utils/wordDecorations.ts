import { CorrectionData, LyricsSegment } from '@/lib/lyrics-review/types'
import { HIGHLIGHT_CLASSES } from '@/lib/lyrics-review/constants'

export type WordCategory = 'anchor' | 'gap' | 'other'

export interface WordDecoration {
  /** Tailwind classes for the timeline bar's background + text colour. */
  barClassName: string
  /** Original transcription text for an AI-corrected word — drawn struck-through above the bar. */
  originalText?: string
}

/**
 * Classify a word as anchor / gap / other and whether it carries a correction, mirroring the
 * per-word logic the Advanced (HighlightedText) view uses. Kept in one place so the Waveforms
 * bars colour-code identically to the Advanced pills.
 */
export function classifyWord(
  data: CorrectionData,
  wordId: string
): { category: WordCategory; isCorrected: boolean } {
  const isCorrected = Boolean(
    data.corrections?.find((c) => c.corrected_word_id === wordId || c.word_id === wordId)
  )

  const isAnchor = Boolean(
    data.anchor_sequences?.find((a) => a.transcribed_word_ids.includes(wordId))
  )
  if (isAnchor) return { category: 'anchor', isCorrected }

  const isGap = Boolean(
    data.gap_sequences?.find((g) => {
      const inTranscribed = g.transcribed_word_ids.includes(wordId)
      const inReference = Object.values(g.reference_word_ids).some((ids) => ids.includes(wordId))
      const isCorrectionInGap = (data.corrections ?? []).some(
        (c) =>
          (c.corrected_word_id === wordId || c.word_id === wordId) &&
          g.transcribed_word_ids.includes(c.word_id)
      )
      return inTranscribed || inReference || isCorrectionInGap
    })
  )

  return { category: isGap ? 'gap' : 'other', isCorrected }
}

/**
 * Pick the timeline-bar colour for a word, matching the Advanced pills EXACTLY: the same
 * HIGHLIGHT_CLASSES tints and the same priority order (currently-playing is handled separately
 * by the timeline). Text stays the default foreground, like the pills. 'other' words get a
 * faint neutral so the bar is still visible over the dark row (the pills can be transparent
 * because they sit inline in text; a timeline bar cannot).
 */
export function barClassForWord(flags: {
  category: WordCategory
  isCorrected: boolean
  isUserEdited: boolean
  isAiCorrected: boolean
}): string {
  if (flags.isAiCorrected) return HIGHLIGHT_CLASSES.aiCorrected
  if (flags.category === 'anchor') return HIGHLIGHT_CLASSES.anchor
  if (flags.isUserEdited) return HIGHLIGHT_CLASSES.userEdited
  if (flags.isCorrected) return HIGHLIGHT_CLASSES.corrected
  if (flags.category === 'gap') return HIGHLIGHT_CLASSES.uncorrectedGap
  return 'bg-foreground/10'
}

/**
 * Build the per-word decoration map (bar colour + AI-correction ghost text) for a segment,
 * keyed by word id, for the Waveforms inline timeline.
 */
export function buildSegmentDecorations(
  data: CorrectionData,
  segment: LyricsSegment,
  opts: {
    editedWordIds?: Set<string>
    aiCorrectedWordIds?: Set<string>
    aiOriginalTextByWordId?: Map<string, string>
  }
): Map<string, WordDecoration> {
  const map = new Map<string, WordDecoration>()
  for (const word of segment.words) {
    const { category, isCorrected } = classifyWord(data, word.id)
    const isAiCorrected = opts.aiCorrectedWordIds?.has(word.id) ?? false
    map.set(word.id, {
      barClassName: barClassForWord({
        category,
        isCorrected,
        isUserEdited: opts.editedWordIds?.has(word.id) ?? false,
        isAiCorrected,
      }),
      originalText: isAiCorrected ? opts.aiOriginalTextByWordId?.get(word.id) : undefined,
    })
  }
  return map
}
