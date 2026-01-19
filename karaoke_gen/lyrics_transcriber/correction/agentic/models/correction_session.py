from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict

from .enums import SessionType, SessionStatus


@dataclass
class CorrectionSession:
    id: str
    audio_file_hash: str
    session_type: SessionType
    ai_model_config: Dict[str, object]
    total_corrections: int
    accepted_corrections: int
    human_modifications: int
    session_duration_ms: int
    accuracy_improvement: float
    started_at: datetime
    completed_at: Optional[datetime]
    status: SessionStatus

    def validate(self) -> None:
        # Basic validations per data-model
        if any(v < 0 for v in (self.total_corrections, self.accepted_corrections, self.human_modifications)):
            raise ValueError("correction counts must be non-negative")
        if self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError("completed_at must be after started_at")


