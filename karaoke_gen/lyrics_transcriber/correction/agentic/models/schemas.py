from __future__ import annotations

from typing import Optional, List
from pydantic import BaseModel, Field, conint, confloat
from enum import Enum


class GapCategory(str, Enum):
    """Categories for gap classification in transcription correction."""
    PUNCTUATION_ONLY = "PUNCTUATION_ONLY"
    SOUND_ALIKE = "SOUND_ALIKE"
    BACKGROUND_VOCALS = "BACKGROUND_VOCALS"
    EXTRA_WORDS = "EXTRA_WORDS"
    REPEATED_SECTION = "REPEATED_SECTION"
    COMPLEX_MULTI_ERROR = "COMPLEX_MULTI_ERROR"
    AMBIGUOUS = "AMBIGUOUS"
    NO_ERROR = "NO_ERROR"


class GapClassification(BaseModel):
    """Classification result for a gap in the transcription."""
    gap_id: str = Field(..., description="Unique identifier for the gap")
    category: GapCategory = Field(..., description="Classification category")
    confidence: confloat(ge=0.0, le=1.0) = Field(..., description="Confidence in classification (0-1)")
    reasoning: str = Field(..., description="Explanation for the classification")
    suggested_handler: Optional[str] = Field(None, description="Recommended handler for this gap")


class CorrectionProposal(BaseModel):
    word_id: Optional[str] = Field(None, description="ID of the word to correct")
    word_ids: Optional[List[str]] = Field(None, description="IDs of multiple words when applicable")
    action: str = Field(..., description="ReplaceWord|SplitWord|DeleteWord|AdjustTiming|NoAction|Flag")
    replacement_text: Optional[str] = Field(None, description="Text to insert/replace with")
    timing_delta_ms: Optional[conint(ge=-1000, le=1000)] = None
    confidence: confloat(ge=0.0, le=1.0) = 0.0
    reason: str = Field(..., description="Short rationale for the proposal")
    gap_category: Optional[GapCategory] = Field(None, description="Classification category of the gap")
    requires_human_review: bool = Field(False, description="Whether this proposal needs human review")
    artist: Optional[str] = Field(None, description="Song artist for context")
    title: Optional[str] = Field(None, description="Song title for context")


class CorrectionProposalList(BaseModel):
    proposals: List[CorrectionProposal]


