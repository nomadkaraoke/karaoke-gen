"""Schemas for correction annotations and human feedback."""

from __future__ import annotations

from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from enum import Enum
from datetime import datetime
import uuid


class CorrectionAnnotationType(str, Enum):
    """Types of corrections that can be annotated."""
    PUNCTUATION_ONLY = "PUNCTUATION_ONLY"
    SOUND_ALIKE = "SOUND_ALIKE"
    BACKGROUND_VOCALS = "BACKGROUND_VOCALS"
    EXTRA_WORDS = "EXTRA_WORDS"
    REPEATED_SECTION = "REPEATED_SECTION"
    COMPLEX_MULTI_ERROR = "COMPLEX_MULTI_ERROR"
    AMBIGUOUS = "AMBIGUOUS"
    NO_ERROR = "NO_ERROR"
    MANUAL_EDIT = "MANUAL_EDIT"  # Human-initiated edit not from gap


class CorrectionAction(str, Enum):
    """Actions that can be taken for corrections."""
    NO_ACTION = "NO_ACTION"
    REPLACE = "REPLACE"
    DELETE = "DELETE"
    INSERT = "INSERT"
    MERGE = "MERGE"
    SPLIT = "SPLIT"
    FLAG = "FLAG"


class CorrectionAnnotation(BaseModel):
    """Annotation for a manual correction made by a human."""
    
    annotation_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Unique identifier")
    audio_hash: str = Field(..., description="Hash of the audio file")
    gap_id: Optional[str] = Field(None, description="Gap ID if this correction is for a gap")
    
    # Classification
    annotation_type: CorrectionAnnotationType = Field(..., description="Type of correction")
    action_taken: CorrectionAction = Field(..., description="Action that was taken")
    
    # Content
    original_text: str = Field(..., description="Original transcribed text")
    corrected_text: str = Field(..., description="Corrected text after human edit")
    
    # Metadata
    confidence: float = Field(..., ge=1.0, le=5.0, description="Human confidence rating (1-5)")
    reasoning: str = Field(..., min_length=10, description="Human explanation for the correction")
    word_ids_affected: List[str] = Field(default_factory=list, description="Word IDs involved in correction")
    
    # Agentic AI comparison
    agentic_proposal: Optional[Dict[str, Any]] = Field(None, description="What the AI suggested (if applicable)")
    agentic_category: Optional[str] = Field(None, description="Category the AI classified this as")
    agentic_agreed: bool = Field(False, description="Whether human agreed with AI proposal")
    
    # Reference lyrics
    reference_sources_consulted: List[str] = Field(default_factory=list, description="Which reference sources were used")
    
    # Song metadata
    artist: str = Field(..., description="Song artist")
    title: str = Field(..., description="Song title")
    session_id: str = Field(..., description="Correction session ID")
    
    # Timestamp
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="When annotation was created")
    
    class Config:
        json_schema_extra = {
            "example": {
                "annotation_id": "550e8400-e29b-41d4-a716-446655440000",
                "audio_hash": "abc123",
                "gap_id": "gap_1",
                "annotation_type": "sound_alike",
                "action_taken": "REPLACE",
                "original_text": "out I'm starting over",
                "corrected_text": "now I'm starting over",
                "confidence": 5.0,
                "reasoning": "The word 'out' sounds like 'now' but the reference lyrics and context make it clear it should be 'now'",
                "word_ids_affected": ["word_123"],
                "agentic_proposal": {"action": "ReplaceWord", "replacement_text": "now"},
                "agentic_category": "sound_alike",
                "agentic_agreed": True,
                "reference_sources_consulted": ["genius", "spotify"],
                "artist": "Rancid",
                "title": "Time Bomb",
                "session_id": "session_abc",
                "timestamp": "2025-01-01T12:00:00"
            }
        }


class AnnotationStatistics(BaseModel):
    """Aggregated statistics from annotations."""
    
    total_annotations: int = 0
    annotations_by_type: Dict[str, int] = Field(default_factory=dict)
    annotations_by_action: Dict[str, int] = Field(default_factory=dict)
    average_confidence: float = 0.0
    agentic_agreement_rate: float = 0.0
    most_common_errors: List[Dict[str, Any]] = Field(default_factory=list)
    songs_annotated: int = 0

