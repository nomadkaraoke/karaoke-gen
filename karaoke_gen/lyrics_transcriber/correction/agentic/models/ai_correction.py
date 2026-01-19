from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .enums import CorrectionType


@dataclass
class AICorrection:
    id: str
    original_text: str
    corrected_text: str
    confidence_score: float
    reasoning: str
    model_used: str
    correction_type: CorrectionType
    processing_time_ms: int
    tokens_used: int
    created_at: datetime
    word_position: int
    session_id: str

    def validate(self) -> None:
        if not (0.0 <= self.confidence_score <= 1.0):
            raise ValueError("confidence_score must be between 0.0 and 1.0")
        if self.original_text == self.corrected_text:
            raise ValueError("original_text and corrected_text must differ")
        if self.processing_time_ms <= 0:
            raise ValueError("processing_time_ms must be positive")


