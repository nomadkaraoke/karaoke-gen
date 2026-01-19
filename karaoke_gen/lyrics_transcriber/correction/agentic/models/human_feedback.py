from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .enums import ReviewerAction, FeedbackCategory


@dataclass
class HumanFeedback:
    id: str
    ai_correction_id: str
    reviewer_action: ReviewerAction
    final_text: Optional[str]
    reason_category: FeedbackCategory
    reason_detail: Optional[str]
    reviewer_confidence: float
    review_time_ms: int
    reviewer_id: Optional[str]
    created_at: datetime
    session_id: str

    def validate(self) -> None:
        if self.reviewer_action == ReviewerAction.MODIFY and not self.final_text:
            raise ValueError("final_text required when action is MODIFY")
        if self.reviewer_confidence is not None and not (0.0 <= self.reviewer_confidence <= 1.0):
            raise ValueError("reviewer_confidence must be between 0.0 and 1.0")
        if self.review_time_ms <= 0:
            raise ValueError("review_time_ms must be positive")


