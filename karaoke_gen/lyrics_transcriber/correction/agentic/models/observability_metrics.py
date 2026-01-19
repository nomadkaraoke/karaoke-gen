from dataclasses import dataclass
from datetime import datetime
from typing import Dict


@dataclass
class ObservabilityMetrics:
    id: str
    session_id: str
    ai_correction_accuracy: float
    processing_time_breakdown: Dict[str, int]
    human_review_duration: int
    model_response_times: Dict[str, int]
    error_reduction_percentage: float
    cost_tracking: Dict[str, float]
    system_health_indicators: Dict[str, float]
    improvement_trends: Dict[str, float]
    recorded_at: datetime

    def validate(self) -> None:
        if not (0.0 <= self.ai_correction_accuracy <= 100.0):
            raise ValueError("ai_correction_accuracy must be 0-100")
        if not (0.0 <= self.error_reduction_percentage <= 100.0):
            raise ValueError("error_reduction_percentage must be 0-100")
        if self.human_review_duration < 0:
            raise ValueError("human_review_duration must be non-negative")


