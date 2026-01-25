from dataclasses import dataclass, asdict, field, fields
from typing import Any, Dict, List, Optional, Set, Tuple
from enum import Enum
from karaoke_gen.lyrics_transcriber.utils.word_utils import WordUtils


@dataclass
class Word:
    """Represents a single word with its timing (in seconds) and confidence information."""

    id: str  # New: Unique identifier for each word
    text: str
    start_time: float
    end_time: float
    confidence: Optional[float] = None
    # New: Track if this word was created during correction
    created_during_correction: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Convert Word to dictionary for JSON serialization."""
        d = asdict(self)
        # Remove confidence from output if it's None
        if d["confidence"] is None:
            del d["confidence"]
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Word":
        """Create Word from dictionary."""
        return cls(
            id=data["id"],
            text=data["text"],
            start_time=data["start_time"],
            end_time=data["end_time"],
            confidence=data.get("confidence"),  # Use get() since confidence is optional
            created_during_correction=data.get("created_during_correction", False),
        )


@dataclass
class LyricsSegment:
    """Represents a segment/line of lyrics with timing information in seconds."""

    id: str  # New: Unique identifier for each segment
    text: str
    words: List[Word]
    start_time: float
    end_time: float

    def to_dict(self) -> Dict[str, Any]:
        """Convert LyricsSegment to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "text": self.text,
            "words": [word.to_dict() for word in self.words],
            "start_time": self.start_time,
            "end_time": self.end_time,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LyricsSegment":
        """Create LyricsSegment from dictionary."""
        return cls(
            id=data["id"],
            text=data["text"],
            words=[Word.from_dict(w) for w in data["words"]],
            start_time=data["start_time"],
            end_time=data["end_time"],
        )


@dataclass
class LyricsMetadata:
    """Standardized metadata for lyrics results."""

    source: str
    track_name: str
    artist_names: str

    # Common metadata fields
    album_name: Optional[str] = None
    duration_ms: Optional[int] = None
    explicit: Optional[bool] = None
    language: Optional[str] = None
    is_synced: bool = False

    # Lyrics provider details
    lyrics_provider: Optional[str] = None
    lyrics_provider_id: Optional[str] = None

    # Provider-specific metadata
    provider_metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert metadata to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LyricsMetadata":
        """Create LyricsMetadata from dictionary."""
        return cls(
            source=data["source"],
            track_name=data["track_name"],
            artist_names=data["artist_names"],
            album_name=data.get("album_name"),
            duration_ms=data.get("duration_ms"),
            explicit=data.get("explicit"),
            language=data.get("language"),
            is_synced=data.get("is_synced", False),
            lyrics_provider=data.get("lyrics_provider"),
            lyrics_provider_id=data.get("lyrics_provider_id"),
            provider_metadata=data.get("provider_metadata", {}),
        )


@dataclass
class LyricsData:
    """Standardized response format for all lyrics providers."""

    segments: List[LyricsSegment]
    metadata: LyricsMetadata
    source: str  # e.g., "genius", "spotify", etc.

    def get_full_text(self) -> str:
        """Get the full lyrics text by joining all segment texts."""
        return "\n".join(segment.text for segment in self.segments)

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary for JSON serialization."""
        return {
            "segments": [segment.to_dict() for segment in self.segments],
            "metadata": self.metadata.to_dict(),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LyricsData":
        """Create LyricsData from dictionary."""
        return cls(
            segments=[LyricsSegment.from_dict(s) for s in data["segments"]],
            metadata=LyricsMetadata.from_dict(data["metadata"]),
            source=data["source"],
        )


@dataclass
class WordCorrection:
    """Details about a single word correction."""

    original_word: str
    corrected_word: str  # Empty string indicates word should be deleted
    source: str  # e.g., "spotify", "genius"
    reason: str  # e.g., "matched_in_3_sources", "high_confidence_match"
    original_position: int = 0  # Default to 0 for backwards compatibility with frontend
    segment_index: int = 0  # Default to 0 since it's often not needed
    confidence: Optional[float] = None
    alternatives: Dict[str, int] = field(default_factory=dict)  # Other possible corrections and their occurrence counts
    is_deletion: bool = False  # New field to explicitly mark deletions
    # New fields for handling word splits
    split_index: Optional[int] = None  # Position in the split sequence (0-based)
    split_total: Optional[int] = None  # Total number of words in split
    # New field to track position after corrections
    corrected_position: Optional[int] = None
    # New fields to match TypeScript interface
    reference_positions: Optional[Dict[str, int]] = None  # Maps source to position in reference text
    length: int = 1  # Default to 1 for single-word corrections
    handler: Optional[str] = None  # Name of the correction handler that created this correction
    # New ID fields for tracking word identity through corrections
    word_id: Optional[str] = None  # ID of the original word being corrected
    corrected_word_id: Optional[str] = None  # ID of the new word after correction

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary representation."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WordCorrection":
        """Create WordCorrection from dictionary."""
        # Filter out any keys that aren't part of the dataclass
        valid_fields = {f.name for f in fields(cls)}
        filtered_data = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered_data)


@dataclass
class TranscriptionData:
    """Structured container for transcription results."""

    segments: List[LyricsSegment]
    words: List[Word]
    text: str
    source: str  # e.g., "whisper", "audioshake"
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert TranscriptionData to dictionary for JSON serialization."""
        return {
            "segments": [segment.to_dict() for segment in self.segments],
            "words": [word.to_dict() for word in self.words],
            "text": self.text,
            "source": self.source,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TranscriptionData":
        """Create TranscriptionData from dictionary."""
        return cls(
            segments=[LyricsSegment.from_dict(s) for s in data["segments"]],
            words=[Word.from_dict(w) for w in data["words"]],
            text=data["text"],
            source=data["source"],
            metadata=data.get("metadata"),
        )


@dataclass
class TranscriptionResult:
    name: str
    priority: int
    result: TranscriptionData


class PhraseType(Enum):
    """Types of phrases we can identify"""

    COMPLETE = "complete"  # Grammatically complete unit
    PARTIAL = "partial"  # Incomplete but valid fragment
    CROSS_BOUNDARY = "cross"  # Crosses natural boundaries


@dataclass
class PhraseScore:
    """Scores for a potential phrase"""

    phrase_type: PhraseType
    natural_break_score: float  # 0-1, how well it respects natural breaks
    length_score: float  # 0-1, how appropriate the length is

    @property
    def total_score(self) -> float:
        """Calculate total score with weights"""
        weights = {PhraseType.COMPLETE: 1.0, PhraseType.PARTIAL: 0.7, PhraseType.CROSS_BOUNDARY: 0.3}
        return weights[self.phrase_type] * 0.5 + self.natural_break_score * 0.3 + self.length_score * 0.2

    def to_dict(self) -> Dict[str, Any]:
        """Convert PhraseScore to dictionary for JSON serialization."""
        return {
            "phrase_type": self.phrase_type.value,  # Convert enum to value for JSON
            "natural_break_score": self.natural_break_score,
            "length_score": self.length_score,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PhraseScore":
        """Create PhraseScore from dictionary."""
        return cls(
            phrase_type=PhraseType(data["phrase_type"]), natural_break_score=data["natural_break_score"], length_score=data["length_score"]
        )


@dataclass
class AnchorSequence:
    """Represents a sequence of words that appears in both transcribed and reference lyrics."""

    id: str  # Unique identifier for this anchor sequence
    transcribed_word_ids: List[str]  # IDs of Word objects from the transcription
    transcription_position: int  # Starting position in transcribed text
    reference_positions: Dict[str, int]  # Source -> position mapping
    reference_word_ids: Dict[str, List[str]]  # Source -> list of Word IDs from reference
    confidence: float
    
    # Backwards compatibility: store original words as text for tests
    _words: Optional[List[str]] = field(default=None, repr=False)

    def __init__(self, *args, **kwargs):
        """Backwards-compatible constructor supporting both old and new APIs."""
        # Check for old API usage (either positional args or 'words' keyword)
        if (len(args) >= 3 and isinstance(args[0], list)) or 'words' in kwargs:
            # Old API: either AnchorSequence(words, ...) or AnchorSequence(words=..., ...)
            if 'words' in kwargs:
                # Keyword argument version
                words = kwargs.pop('words')
                transcription_position = kwargs.pop('transcription_position', 0)
                reference_positions = kwargs.pop('reference_positions', {})
                confidence = kwargs.pop('confidence', 0.0)
            else:
                # Positional argument version (may have confidence as keyword)
                words = args[0]
                transcription_position = args[1] if len(args) > 1 else 0
                reference_positions = args[2] if len(args) > 2 else {}
                
                # Handle confidence - could be positional or keyword
                if len(args) > 3:
                    confidence = args[3]
                else:
                    confidence = kwargs.pop('confidence', 0.0)
            
            # Store words for backwards compatibility
            self._words = words
            
            # Create new API fields
            self.id = kwargs.get('id', WordUtils.generate_id())
            self.transcribed_word_ids = [WordUtils.generate_id() for _ in words]
            self.transcription_position = transcription_position
            self.reference_positions = reference_positions
            # Create reference_word_ids with same structure as reference_positions
            self.reference_word_ids = {source: [WordUtils.generate_id() for _ in words] 
                                     for source in reference_positions.keys()}
            self.confidence = confidence
        else:
            # New API: use keyword arguments
            self.id = kwargs.get('id', args[0] if len(args) > 0 else WordUtils.generate_id())
            self.transcribed_word_ids = kwargs.get('transcribed_word_ids', args[1] if len(args) > 1 else [])
            self.transcription_position = kwargs.get('transcription_position', args[2] if len(args) > 2 else 0)
            self.reference_positions = kwargs.get('reference_positions', args[3] if len(args) > 3 else {})
            self.reference_word_ids = kwargs.get('reference_word_ids', args[4] if len(args) > 4 else {})
            self.confidence = kwargs.get('confidence', args[5] if len(args) > 5 else 0.0)
            self._words = kwargs.get('_words', None)

    @property
    def words(self) -> List[str]:
        """Get the words as a list of strings (backwards compatibility)."""
        if self._words is not None:
            return self._words
        # If we don't have stored words, we can't resolve IDs without a word map
        # This is a limitation of the backwards compatibility
        return [f"word_{i}" for i in range(len(self.transcribed_word_ids))]

    @property
    def text(self) -> str:
        """Get the sequence as a space-separated string."""
        return " ".join(self.words)

    @property
    def length(self) -> int:
        """Get the number of words in the sequence."""
        return len(self.transcribed_word_ids)

    def to_dict(self) -> Dict[str, Any]:
        """Convert the anchor sequence to a JSON-serializable dictionary."""
        # Always return the new format that includes all required fields
        result = {
            "id": self.id,
            "transcribed_word_ids": self.transcribed_word_ids,
            "transcription_position": self.transcription_position,
            "reference_positions": self.reference_positions,
            "reference_word_ids": self.reference_word_ids,
            "confidence": self.confidence,
        }
        
        # For backwards compatibility, include words and text fields if _words is present
        if self._words is not None:
            result.update({
                "words": self._words,
                "text": self.text,
                "length": self.length,
            })
        
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AnchorSequence":
        """Create AnchorSequence from dictionary."""
        # Handle both old and new dictionary formats
        # Check for new format keys FIRST (they take priority even if old keys also present)
        if "transcribed_word_ids" in data:
            # New format - use existing IDs
            return cls(
                id=data.get("id", WordUtils.generate_id()),
                transcribed_word_ids=data["transcribed_word_ids"],
                transcription_position=data["transcription_position"],
                reference_positions=data["reference_positions"],
                reference_word_ids=data["reference_word_ids"],
                confidence=data["confidence"],
            )
        elif "words" in data:
            # Old format only - convert to new format by generating IDs
            # This ensures to_dict() always returns the new format
            words = data["words"]
            return cls(
                id=data.get("id", WordUtils.generate_id()),
                transcribed_word_ids=[WordUtils.generate_id() for _ in words],
                transcription_position=data["transcription_position"],
                reference_positions=data["reference_positions"],
                reference_word_ids={source: [WordUtils.generate_id() for _ in words] 
                                   for source in data["reference_positions"].keys()},
                confidence=data["confidence"],
            )
        else:
            raise ValueError("AnchorSequence.from_dict requires either 'transcribed_word_ids' or 'words' key")


@dataclass
class ScoredAnchor:
    """An anchor sequence with its quality score"""

    anchor: AnchorSequence
    phrase_score: PhraseScore

    @property
    def total_score(self) -> float:
        """Combine confidence, phrase quality, and length"""
        # Length bonus: (length - 1) * 0.1 gives 0.1 per extra word
        length_bonus = (self.anchor.length - 1) * 0.1
        # Base score heavily weighted towards confidence
        base_score = self.anchor.confidence * 0.8 + self.phrase_score.total_score * 0.2
        # Combine scores
        return base_score + length_bonus

    def to_dict(self) -> Dict[str, Any]:
        """Convert the scored anchor to a JSON-serializable dictionary."""
        return {
            **self.anchor.to_dict(),
            "phrase_score": {
                "phrase_type": self.phrase_score.phrase_type.value,
                "natural_break_score": self.phrase_score.natural_break_score,
                "length_score": self.phrase_score.length_score,
                "total_score": self.phrase_score.total_score,
            },
            "total_score": self.total_score,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScoredAnchor":
        """Create ScoredAnchor from dictionary."""
        return cls(anchor=AnchorSequence.from_dict(data["anchor"]), phrase_score=PhraseScore.from_dict(data["phrase_score"]))


@dataclass
class GapSequence:
    """Represents a sequence of words between anchor sequences in transcribed lyrics."""

    id: str  # Unique identifier for this gap sequence
    transcribed_word_ids: List[str]  # IDs of Word objects from the transcription
    transcription_position: int  # Original starting position in transcription
    preceding_anchor_id: Optional[str]  # ID of preceding AnchorSequence
    following_anchor_id: Optional[str]  # ID of following AnchorSequence
    reference_word_ids: Dict[str, List[str]]  # Source -> list of Word IDs from reference
    _corrected_positions: Set[int] = field(default_factory=set, repr=False)
    _position_offset: int = field(default=0, repr=False)  # Track cumulative position changes
    
    # Backwards compatibility: store original words as text for tests  
    _words: Optional[List[str]] = field(default=None, repr=False)

    def __init__(self, *args, **kwargs):
        """Backwards-compatible constructor supporting both old and new APIs."""
        if len(args) >= 5 and isinstance(args[0], (list, tuple)):
            # Old API: GapSequence(words, transcription_position, preceding_anchor, following_anchor, reference_words)
            words, transcription_position, preceding_anchor, following_anchor, reference_words = args[:5]
            
            # Store words for backwards compatibility
            self._words = list(words) if isinstance(words, tuple) else words
            
            # Create new API fields
            self.id = kwargs.get('id', WordUtils.generate_id())
            self.transcribed_word_ids = [WordUtils.generate_id() for _ in self._words]
            self.transcription_position = transcription_position
            self.preceding_anchor_id = getattr(preceding_anchor, 'id', None) if preceding_anchor else None
            self.following_anchor_id = getattr(following_anchor, 'id', None) if following_anchor else None
            # Convert reference_words to reference_word_ids
            self.reference_word_ids = {source: [WordUtils.generate_id() for _ in ref_words] 
                                     for source, ref_words in reference_words.items()}
            self._corrected_positions = set()
            self._position_offset = 0
        else:
            # New API: use keyword arguments
            self.id = kwargs.get('id', args[0] if len(args) > 0 else WordUtils.generate_id())
            self.transcribed_word_ids = kwargs.get('transcribed_word_ids', args[1] if len(args) > 1 else [])
            self.transcription_position = kwargs.get('transcription_position', args[2] if len(args) > 2 else 0)
            self.preceding_anchor_id = kwargs.get('preceding_anchor_id', args[3] if len(args) > 3 else None)
            self.following_anchor_id = kwargs.get('following_anchor_id', args[4] if len(args) > 4 else None)
            self.reference_word_ids = kwargs.get('reference_word_ids', args[5] if len(args) > 5 else {})
            self._corrected_positions = kwargs.get('_corrected_positions', set())
            self._position_offset = kwargs.get('_position_offset', 0)
            self._words = kwargs.get('_words', None)

    @property
    def words(self) -> List[str]:
        """Get the words as a list of strings (backwards compatibility)."""
        if self._words is not None:
            return self._words
        # If we don't have stored words, we can't resolve IDs without a word map
        return [f"word_{i}" for i in range(len(self.transcribed_word_ids))]

    @property
    def text(self) -> str:
        """Get the sequence as a space-separated string."""
        return " ".join(self.words)

    @property
    def length(self) -> int:
        """Get the number of words in the sequence."""
        return len(self.transcribed_word_ids)

    def to_dict(self) -> Dict[str, Any]:
        """Convert the gap sequence to a JSON-serializable dictionary."""
        result = {
            "id": self.id,
            "transcribed_word_ids": self.transcribed_word_ids,
            "transcription_position": self.transcription_position,
            "preceding_anchor_id": self.preceding_anchor_id,
            "following_anchor_id": self.following_anchor_id,
            "reference_word_ids": self.reference_word_ids,
        }
        
        # For backwards compatibility, include words and text in dict
        if self._words is not None:
            result.update({
                "words": self._words,
                "text": self.text,
                "length": self.length,
            })
        
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GapSequence":
        """Create GapSequence from dictionary."""
        # Handle both old and new dictionary formats
        if "words" in data:
            # Old format - use backwards compatible constructor
            return cls(
                data["words"],
                data["transcription_position"],
                None,  # preceding_anchor
                None,  # following_anchor
                data.get("reference_words", {}),
                id=data.get("id", WordUtils.generate_id())
            )
        else:
            # New format
            gap = cls(
                id=data.get("id", WordUtils.generate_id()),
                transcribed_word_ids=data["transcribed_word_ids"],
                transcription_position=data["transcription_position"],
                preceding_anchor_id=data["preceding_anchor_id"],
                following_anchor_id=data["following_anchor_id"],
                reference_word_ids=data["reference_word_ids"],
            )
            return gap


@dataclass
class CorrectionStep:
    """Represents a single correction operation with enough info to replay/undo."""

    handler_name: str
    affected_word_ids: List[str]  # IDs of words modified/deleted
    affected_segment_ids: List[str]  # IDs of segments modified
    corrections: List[WordCorrection]
    # State before and after for affected segments
    segments_before: List[LyricsSegment]
    segments_after: List[LyricsSegment]
    # For splits/merges
    created_word_ids: List[str] = field(default_factory=list)  # New words created
    deleted_word_ids: List[str] = field(default_factory=list)  # Words removed

    def to_dict(self) -> Dict[str, Any]:
        """Convert CorrectionStep to dictionary for JSON serialization."""
        return {
            "handler_name": self.handler_name,
            "affected_word_ids": self.affected_word_ids,
            "affected_segment_ids": self.affected_segment_ids,
            "corrections": [c.to_dict() for c in self.corrections],
            "segments_before": [s.to_dict() for s in self.segments_before],
            "segments_after": [s.to_dict() for s in self.segments_after],
            "created_word_ids": self.created_word_ids,
            "deleted_word_ids": self.deleted_word_ids,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CorrectionStep":
        """Create CorrectionStep from dictionary."""
        return cls(
            handler_name=data["handler_name"],
            affected_word_ids=data["affected_word_ids"],
            affected_segment_ids=data["affected_segment_ids"],
            corrections=[WordCorrection.from_dict(c) for c in data["corrections"]],
            segments_before=[LyricsSegment.from_dict(s) for s in data["segments_before"]],
            segments_after=[LyricsSegment.from_dict(s) for s in data["segments_after"]],
            created_word_ids=data["created_word_ids"],
            deleted_word_ids=data["deleted_word_ids"],
        )


@dataclass
class CorrectionResult:
    """Container for correction results with detailed correction information."""

    # Original (uncorrected) data
    original_segments: List[LyricsSegment]

    # Corrected data
    corrected_segments: List[LyricsSegment]

    # Correction details
    corrections: List[WordCorrection]
    corrections_made: int
    confidence: float

    # Debug/analysis information
    reference_lyrics: Dict[str, LyricsData]  # Maps source to LyricsData
    anchor_sequences: List[AnchorSequence]
    gap_sequences: List[GapSequence]
    resized_segments: List[LyricsSegment]

    metadata: Dict[str, Any]

    # Correction history
    correction_steps: List[CorrectionStep]
    word_id_map: Dict[str, str]  # Maps original word IDs to corrected word IDs
    segment_id_map: Dict[str, str]  # Maps original segment IDs to corrected segment IDs

    def to_dict(self) -> Dict[str, Any]:
        """Convert the correction result to a JSON-serializable dictionary."""
        return {
            "original_segments": [s.to_dict() for s in self.original_segments] if self.original_segments else [],
            "reference_lyrics": {source: lyrics.to_dict() for source, lyrics in self.reference_lyrics.items()} if self.reference_lyrics else {},
            "anchor_sequences": [a.to_dict() for a in self.anchor_sequences] if self.anchor_sequences else [],
            "gap_sequences": [g.to_dict() for g in self.gap_sequences] if self.gap_sequences else [],
            "resized_segments": [s.to_dict() for s in self.resized_segments] if self.resized_segments else [],
            "corrections_made": self.corrections_made,
            "confidence": self.confidence,
            "corrections": [c.to_dict() for c in self.corrections] if self.corrections else [],
            "corrected_segments": [s.to_dict() for s in self.corrected_segments] if self.corrected_segments else [],
            "metadata": self.metadata or {},
            "correction_steps": [step.to_dict() for step in self.correction_steps] if self.correction_steps else [],
            "word_id_map": self.word_id_map or {},
            "segment_id_map": self.segment_id_map or {},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CorrectionResult":
        """Create CorrectionResult from dictionary."""
        return cls(
            original_segments=[LyricsSegment.from_dict(s) for s in data["original_segments"]],
            corrected_segments=[LyricsSegment.from_dict(s) for s in data["corrected_segments"]],
            corrections=[WordCorrection.from_dict(c) for c in data["corrections"]],
            corrections_made=data["corrections_made"],
            confidence=data["confidence"],
            reference_lyrics={source: LyricsData.from_dict(lyrics) for source, lyrics in data["reference_lyrics"].items()},
            anchor_sequences=[AnchorSequence.from_dict(a) for a in data["anchor_sequences"]],
            gap_sequences=[GapSequence.from_dict(g) for g in data["gap_sequences"]],
            resized_segments=[LyricsSegment.from_dict(s) for s in data["resized_segments"]],
            metadata=data["metadata"],
            correction_steps=[CorrectionStep.from_dict(step) for step in data["correction_steps"]],
            word_id_map=data["word_id_map"],
            segment_id_map=data["segment_id_map"],
        )
