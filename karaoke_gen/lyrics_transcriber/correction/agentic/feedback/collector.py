from __future__ import annotations

from typing import Dict, Any

from .store import FeedbackStore


class FeedbackCollector:
    def __init__(self, store: FeedbackStore | None):
        self._store = store

    def collect(self, feedback_id: str, session_id: str | None, data_json: str) -> None:
        if not self._store:
            return
        self._store.put_feedback(feedback_id, session_id, data_json)


