'use client'

import { useCallback } from 'react'
import {
  AnchorSequence,
  GapSequence,
  InteractionMode,
  WordCorrection,
  ModalContent,
  WordClickInfo,
} from '@/lib/lyrics-review/types'

export interface UseWordClickProps {
  mode: InteractionMode
  onElementClick: (content: ModalContent) => void
  onWordClick?: (info: WordClickInfo) => void
  isReference?: boolean
  currentSource?: string
  gaps?: GapSequence[]
  anchors?: AnchorSequence[]
  corrections?: WordCorrection[]
}

export function useWordClick({
  mode,
  onElementClick,
  onWordClick,
  isReference = false,
  currentSource = '',
  gaps = [],
  anchors = [],
  corrections = [],
}: UseWordClickProps) {
  const handleWordClick = useCallback(
    (word: string, wordId: string, anchor?: AnchorSequence, gap?: GapSequence) => {
      // Check if word belongs to anchor
      const belongsToAnchor =
        anchor &&
        (isReference
          ? anchor.reference_word_ids[currentSource]?.includes(wordId)
          : anchor.transcribed_word_ids.includes(wordId))

      // Find matching gap if not provided
      const matchingGap =
        gap ||
        gaps.find(
          (g) =>
            g.transcribed_word_ids.includes(wordId) ||
            Object.values(g.reference_word_ids).some((ids) => ids.includes(wordId))
        )

      // Check if word belongs to gap - include both original and corrected words
      const belongsToGap =
        matchingGap &&
        (isReference
          ? matchingGap.reference_word_ids[currentSource]?.includes(wordId)
          : matchingGap.transcribed_word_ids.includes(wordId) ||
            corrections.some((c) => c.corrected_word_id === wordId || c.word_id === wordId))

      // For reference view clicks, find the corresponding gap
      if (isReference && currentSource) {
        const refMatchingGap = gaps?.find((g) =>
          g.reference_word_ids[currentSource]?.includes(wordId)
        )
        if (refMatchingGap) {
          gap = refMatchingGap
        }
      }

      if (mode === 'highlight' || mode === 'edit' || mode === 'delete_word') {
        if (belongsToAnchor && anchor) {
          onWordClick?.({
            word_id: wordId,
            type: 'anchor',
            anchor,
            gap: undefined,
          })
        } else if (belongsToGap && gap) {
          onWordClick?.({
            word_id: wordId,
            type: 'gap',
            anchor: undefined,
            gap,
          })
        } else if (
          corrections.some(
            (c) =>
              (c.corrected_word_id === wordId || c.word_id === wordId) &&
              gap?.transcribed_word_ids.includes(c.word_id)
          )
        ) {
          // If the word is part of a correction, mark it as a gap
          onWordClick?.({
            word_id: wordId,
            type: 'gap',
            anchor: undefined,
            gap,
          })
        } else {
          onWordClick?.({
            word_id: wordId,
            type: 'other',
            anchor: undefined,
            gap: undefined,
          })
        }
      } else {
        // This is a fallback for any future modes
        if (belongsToAnchor && anchor) {
          onElementClick({
            type: 'anchor',
            data: {
              ...anchor,
              wordId,
              word,
              anchor_sequences: anchors,
            },
          })
        } else if (belongsToGap && gap) {
          onElementClick({
            type: 'gap',
            data: {
              ...gap,
              wordId,
              word,
              anchor_sequences: anchors,
            },
          })
        } else if (!isReference) {
          // Create synthetic gap for non-sequence words (transcription view only)
          const syntheticGap: GapSequence = {
            id: `synthetic-${wordId}`,
            transcribed_word_ids: [wordId],
            transcription_position: -1,
            preceding_anchor_id: null,
            following_anchor_id: null,
            reference_word_ids: {},
          }
          onElementClick({
            type: 'gap',
            data: {
              ...syntheticGap,
              wordId,
              word,
              anchor_sequences: anchors,
            },
          })
        }
      }
    },
    [mode, onWordClick, onElementClick, isReference, currentSource, gaps, anchors, corrections]
  )

  return { handleWordClick }
}
