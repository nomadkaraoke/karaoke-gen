from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict


@dataclass
class LearningData:
    id: str
    session_id: str
    error_patterns: Dict[str, int]
    correction_strategies: Dict[str, int]
    model_performance: Dict[str, float]
    feedback_trends: Dict[str, int]
    improvement_metrics: Dict[str, float]
    data_quality_score: float
    created_at: datetime
    expires_at: datetime

    def validate(self) -> None:
        if not (0.0 <= self.data_quality_score <= 1.0):
            raise ValueError("data_quality_score must be between 0.0 and 1.0")
        # Note: exact 3-year check depends on business rule; enforce >= 3 years
        if (self.expires_at - self.created_at).days < 365 * 3:
            raise ValueError("expires_at must be at least 3 years from created_at")


