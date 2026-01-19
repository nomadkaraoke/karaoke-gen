import { z } from 'zod'

// Define schemas that match our TypeScript interfaces
const WordSchema = z.object({
  id: z.string(),
  text: z.string(),
  start_time: z.number().nullable(),
  end_time: z.number().nullable(),
  confidence: z.number().optional(),
  created_during_correction: z.boolean().optional(),
})

const LyricsSegmentSchema = z.object({
  id: z.string(),
  text: z.string(),
  words: z.array(WordSchema),
  start_time: z.number().nullable(),
  end_time: z.number().nullable(),
})

const WordCorrectionSchema = z.object({
  id: z.string().optional(),
  handler: z.string(),
  original_word: z.string(),
  corrected_word: z.string(),
  segment_id: z.string().optional(),
  word_id: z.string(),
  corrected_word_id: z.string().nullable(),
  source: z.string(),
  confidence: z.number(),
  reason: z.string(),
  alternatives: z.record(z.number()),
  is_deletion: z.boolean(),
  split_index: z.number().nullable().optional(),
  split_total: z.number().nullable().optional(),
  reference_positions: z.record(z.number()).optional(),
  length: z.number(),
})

const ReferenceSourceSchema = z.object({
  segments: z.array(LyricsSegmentSchema),
  metadata: z.object({
    source: z.string(),
    track_name: z.string().nullable(),
    artist_names: z.string().nullable(),
    album_name: z.string().nullable(),
    duration_ms: z.number().nullable(),
    explicit: z.boolean().nullable(),
    language: z.string().nullable(),
    is_synced: z.boolean(),
    lyrics_provider: z.string(),
    lyrics_provider_id: z.string(),
    provider_metadata: z.record(z.unknown()),
  }),
  source: z.string(),
})

const PhraseScoreSchema = z.object({
  phrase_type: z.string(),
  natural_break_score: z.number(),
  length_score: z.number(),
  total_score: z.number(),
})

const AnchorSequenceSchema = z.object({
  id: z.string(),
  transcribed_word_ids: z.array(z.string()),
  transcription_position: z.number(),
  reference_positions: z.record(z.number()),
  reference_word_ids: z.record(z.array(z.string())),
  confidence: z.number(),
  phrase_score: PhraseScoreSchema.optional(),
  total_score: z.number().optional(),
})

const GapSequenceSchema = z.object({
  id: z.string(),
  transcribed_word_ids: z.array(z.string()),
  transcription_position: z.number(),
  preceding_anchor_id: z.string().nullable(),
  following_anchor_id: z.string().nullable(),
  reference_word_ids: z.record(z.array(z.string())),
})

const CorrectionStepSchema = z.object({
  handler_name: z.string(),
  affected_word_ids: z.array(z.string()),
  affected_segment_ids: z.array(z.string()),
  corrections: z.array(WordCorrectionSchema),
  segments_before: z.array(LyricsSegmentSchema),
  segments_after: z.array(LyricsSegmentSchema),
  created_word_ids: z.array(z.string()),
  deleted_word_ids: z.array(z.string()),
})

const CorrectionHandlerSchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string(),
  enabled: z.boolean(),
})

const CorrectionDataSchema = z.object({
  original_segments: z.array(LyricsSegmentSchema),
  reference_lyrics: z.record(ReferenceSourceSchema),
  anchor_sequences: z.array(AnchorSequenceSchema),
  gap_sequences: z.array(GapSequenceSchema),
  resized_segments: z.array(LyricsSegmentSchema),
  corrections_made: z.number(),
  confidence: z.number(),
  corrections: z.array(WordCorrectionSchema),
  corrected_segments: z.array(LyricsSegmentSchema),
  metadata: z.object({
    anchor_sequences_count: z.number(),
    gap_sequences_count: z.number(),
    total_words: z.number(),
    correction_ratio: z.number(),
    audio_filepath: z.string().optional(),
    audio_hash: z.string().optional(),
    available_handlers: z.array(CorrectionHandlerSchema).optional(),
    enabled_handlers: z.array(z.string()).optional(),
  }),
  correction_steps: z.array(CorrectionStepSchema),
  word_id_map: z.record(z.string()),
  segment_id_map: z.record(z.string()),
})

export function validateCorrectionData(data: unknown) {
  return CorrectionDataSchema.parse(data)
}
