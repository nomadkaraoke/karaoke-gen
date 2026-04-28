// Core types for lyrics review functionality

// Singer id. 1 = Singer 1, 2 = Singer 2, 0 = Both. undefined = default (Singer 1).
export type SingerId = 0 | 1 | 2

export interface Word {
  id: string
  text: string
  start_time: number | null
  end_time: number | null
  confidence?: number
  created_during_correction?: boolean
  singer?: SingerId
}

export interface LyricsSegment {
  id: string
  text: string
  words: Word[]
  start_time: number | null
  end_time: number | null
  singer?: SingerId
}

export interface WordCorrection {
  id?: string
  handler: string
  original_word: string
  corrected_word: string
  segment_id?: string
  word_id: string
  corrected_word_id: string | null
  source: string
  confidence: number
  reason: string
  alternatives: Record<string, number>
  is_deletion: boolean
  split_index?: number | null
  split_total?: number | null
  reference_positions?: Record<string, number>
  length: number
}

export interface PhraseScore {
  phrase_type: string
  natural_break_score: number
  length_score: number
  total_score: number
}

export interface AnchorSequence {
  id: string
  transcribed_word_ids: string[]
  transcription_position: number
  reference_positions: Record<string, number>
  reference_word_ids: {
    [source: string]: string[]
  }
  confidence: number
  phrase_score?: PhraseScore
  total_score?: number
}

export interface GapSequence {
  id: string
  transcribed_word_ids: string[]
  transcription_position: number
  preceding_anchor_id: string | null
  following_anchor_id: string | null
  reference_word_ids: {
    [source: string]: string[]
  }
}

export interface ReferenceSource {
  segments: LyricsSegment[]
  metadata: {
    source: string
    track_name: string | null
    artist_names: string | null
    album_name: string | null
    duration_ms: number | null
    explicit: boolean | null
    language: string | null
    is_synced: boolean
    lyrics_provider: string
    lyrics_provider_id: string
    provider_metadata: Record<string, unknown>
  }
  source: string
}

export interface CorrectionStep {
  handler_name: string
  affected_word_ids: string[]
  affected_segment_ids: string[]
  corrections: WordCorrection[]
  segments_before: LyricsSegment[]
  segments_after: LyricsSegment[]
  created_word_ids: string[]
  deleted_word_ids: string[]
}

export interface CorrectionHandler {
  id: string
  name: string
  description: string
  enabled: boolean
}

export interface CorrectionData {
  original_segments: LyricsSegment[]
  reference_lyrics: Record<string, ReferenceSource>
  anchor_sequences: AnchorSequence[]
  gap_sequences: GapSequence[]
  resized_segments: LyricsSegment[]
  corrections_made: number
  confidence: number
  corrections: WordCorrection[]
  corrected_segments: LyricsSegment[]
  metadata: {
    anchor_sequences_count: number
    gap_sequences_count: number
    total_words: number
    correction_ratio: number
    audio_filepath?: string
    audio_hash?: string
    available_handlers?: CorrectionHandler[]
    enabled_handlers?: string[]
    artist?: string
    title?: string
  }
  correction_steps: CorrectionStep[]
  word_id_map: Record<string, string>
  segment_id_map: Record<string, string>
  is_duet?: boolean
  // Instrumental selection data (added for combined review flow)
  instrumental_options?: InstrumentalOption[]
  backing_vocals_analysis?: BackingVocalsAnalysis | null
}

/** Instrumental option from the combined review endpoint */
export interface InstrumentalOption {
  id: string // "clean" | "with_backing"
  label: string
  audio_url: string | null
}

/** Backing vocals analysis data */
export interface BackingVocalsAnalysis {
  has_audible_content: boolean
  total_duration_seconds: number
  audible_segments: Array<{
    start_seconds: number
    end_seconds: number
    duration_seconds: number
  }>
  recommended_selection: string // "clean" | "with_backing" | "review_needed"
  total_audible_duration_seconds: number
  audible_percentage: number
  silence_threshold_db: number
}

export interface HighlightInfo {
  type: 'single' | 'gap' | 'anchor' | 'correction'
  sequence?: AnchorSequence | GapSequence
  transcribed_words: Word[]
  reference_words?: {
    [source: string]: Word[]
  }
  correction?: WordCorrection
}

export type InteractionMode = 'highlight' | 'edit' | 'delete_word'

// Edit Log Types — captures user edits for training data / feedback
export type EditOperationType =
  | 'word_change' | 'word_delete' | 'word_add'
  | 'word_split' | 'word_merge'
  | 'segment_delete' | 'segment_add' | 'segment_split' | 'segment_merge'
  | 'find_replace' | 'replace_all_lyrics' | 'change_case' | 'custom_lyrics_replace'
  | 'timing_change' | 'revert_correction' | 'revert_all'

export type EditFeedbackReason =
  | 'misheard_word' | 'wrong_lyrics' | 'spelling_punctuation'
  | 'phantom_word' | 'duplicate_word'
  | 'missing_word' | 'split_word'
  | 'other' | 'no_response'

export interface EditFeedback {
  reason: EditFeedbackReason
  timestamp: string
}

export interface EditLogEntry {
  id: string
  timestamp: string
  operation: EditOperationType
  segment_id: string | null
  segment_index: number | null
  word_ids_before: string[]
  word_ids_after: string[]
  text_before: string
  text_after: string
  details?: Record<string, unknown>
  feedback?: EditFeedback | null
}

export interface EditLog {
  session_id: string
  job_id: string
  audio_hash: string
  started_at: string
  entries: EditLogEntry[]
}

// Correction Annotation Types
export type CorrectionAnnotationType =
  | 'PUNCTUATION_ONLY'
  | 'SOUND_ALIKE'
  | 'BACKGROUND_VOCALS'
  | 'EXTRA_WORDS'
  | 'REPEATED_SECTION'
  | 'COMPLEX_MULTI_ERROR'
  | 'AMBIGUOUS'
  | 'NO_ERROR'
  | 'MANUAL_EDIT'

export type CorrectionAction =
  | 'NO_ACTION'
  | 'REPLACE'
  | 'DELETE'
  | 'INSERT'
  | 'MERGE'
  | 'SPLIT'
  | 'FLAG'

export interface CorrectionAnnotation {
  annotation_id: string
  audio_hash: string
  gap_id: string | null
  annotation_type: CorrectionAnnotationType
  action_taken: CorrectionAction
  original_text: string
  corrected_text: string
  confidence: number // 1-5 scale
  reasoning: string
  word_ids_affected: string[]
  agentic_proposal: {
    action: string
    replacement_text?: string
    confidence: number
    reason: string
    gap_category?: string
  } | null
  agentic_category: string | null
  agentic_agreed: boolean
  reference_sources_consulted: string[]
  artist: string
  title: string
  session_id: string
  timestamp?: string
}

export interface CorrectionActionEvent {
  type: 'revert' | 'edit' | 'accept' | 'reject'
  correctionId: string
  wordId: string
}

export interface GapCategoryMetric {
  category: string
  count: number
  avgConfidence: number
}

// Modal content types (from LyricsAnalyzer)
export type ModalContent =
  | {
      type: 'anchor'
      data: AnchorSequence & {
        wordId: string
        word?: string
        anchor_sequences: AnchorSequence[]
      }
    }
  | {
      type: 'gap'
      data: GapSequence & {
        wordId: string
        word: string
        anchor_sequences: AnchorSequence[]
      }
    }

// Shared view types
export type FlashType = 'anchor' | 'corrected' | 'uncorrected' | 'word' | 'handler' | null

export interface WordClickInfo {
  word_id: string
  type: 'anchor' | 'gap' | 'other'
  anchor?: AnchorSequence
  gap?: GapSequence
}

export interface BaseViewProps {
  onElementClick: (content: ModalContent) => void
  onWordClick?: (info: WordClickInfo) => void
  flashingType: FlashType
  highlightInfo: HighlightInfo | null
  mode: InteractionMode
}

export interface BaseWordPosition {
  type: 'anchor' | 'gap' | 'other'
  sequence?: AnchorSequence | GapSequence
}

export interface TranscriptionWordPosition extends BaseWordPosition {
  word: {
    id: string
    text: string
    start_time?: number
    end_time?: number
  }
  type: 'anchor' | 'gap' | 'other'
  sequence?: AnchorSequence | GapSequence
  isInRange: boolean
  isCorrected?: boolean
}

export interface ReferenceWordPosition extends BaseWordPosition {
  index: number
  isHighlighted: boolean
  word: string // Simple string word for reference view
}

export interface WordProps {
  word: string
  shouldFlash: boolean
  isAnchor?: boolean
  isCorrectedGap?: boolean
  isUncorrectedGap?: boolean
  isCurrentlyPlaying?: boolean
  isActiveGap?: boolean
  isUserEdited?: boolean
  padding?: string
  onClick?: () => void
  id?: string
  correction?: {
    originalWord: string
    handler: string
    confidence: number
    source: string
    reason?: string
  } | null
}

export interface TextSegmentProps extends BaseViewProps {
  wordPositions: TranscriptionWordPosition[] | ReferenceWordPosition[]
}

export interface LinePosition {
  position: number
  lineNumber: number
  isEmpty?: boolean
}

export interface TranscriptionViewProps {
  data: CorrectionData
  onElementClick: (content: ModalContent) => void
  onWordClick?: (info: WordClickInfo) => void
  flashingType: FlashType
  highlightInfo: HighlightInfo | null
  mode: InteractionMode
  onPlaySegment?: (startTime: number) => void
  currentTime?: number
  anchors?: AnchorSequence[]
  flashingHandler?: string | null
  onDataChange?: (updatedData: CorrectionData) => void
  // Gap navigation
  activeGapWordIds?: Set<string>
  // Review mode props for agentic corrections
  reviewMode?: boolean
  onRevertCorrection?: (wordId: string) => void
  onEditCorrection?: (wordId: string) => void
  onAcceptCorrection?: (wordId: string) => void
  onShowCorrectionDetail?: (wordId: string) => void
  advancedMode?: boolean
  onAdvancedModeToggle?: (enabled: boolean) => void
  editedWordIds?: Set<string>
  isDuet?: boolean
  onSegmentSingerChange?: (segmentIdx: number, next: SingerId) => void
  onSegmentFocus?: (segmentIndex: number | null) => void
}

export interface ReferenceViewProps extends BaseViewProps {
  referenceSources: Record<string, ReferenceSource>
  anchors: CorrectionData['anchor_sequences']
  gaps: CorrectionData['gap_sequences']
  currentSource: string
  onSourceChange: (source: string) => void
  corrected_segments: LyricsSegment[]
  corrections: WordCorrection[]
  onAddLyrics?: () => void
  onAddLyricsInline?: (source: string, lyrics: string) => Promise<void>
  onSearchLyrics?: (artist: string, title: string, forceSources: string[]) => Promise<SearchLyricsResponse>
  defaultArtist?: string
  defaultTitle?: string
}

export interface RejectedSource {
  relevance: number
  matched_words: number
  total_words: number
  track_name: string
  artist_names: string
}

export interface SearchLyricsResponse {
  status: 'success' | 'no_results'
  data?: CorrectionData
  message?: string
  sources_added: string[]
  sources_rejected: Record<string, RejectedSource>
  sources_not_found: string[]
}

export interface HighlightedTextProps extends BaseViewProps {
  text?: string
  segments?: LyricsSegment[]
  wordPositions: TranscriptionWordPosition[] | ReferenceWordPosition[]
  anchors: AnchorSequence[]
  highlightInfo: HighlightInfo | null
  mode: InteractionMode
  onElementClick: (content: ModalContent) => void
  onWordClick?: (info: WordClickInfo) => void
  flashingType: FlashType
  isReference?: boolean
  currentSource?: string
  preserveSegments?: boolean
  linePositions?: LinePosition[]
  currentTime?: number
  referenceCorrections?: Map<string, string>
  gaps?: GapSequence[]
  flashingHandler?: string | null
  corrections?: WordCorrection[]
}
