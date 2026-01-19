from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Any


@dataclass
class MetricsAggregator:
    """In-memory metrics aggregator for agentic correction API."""

    total_sessions: int = 0
    total_processing_time_ms: int = 0
    total_feedback: int = 0
    model_counts: Dict[str, int] = field(default_factory=dict)
    model_total_time_ms: Dict[str, int] = field(default_factory=dict)
    fallback_count: int = 0

    def record_session(self, model_id: str, processing_time_ms: int, fallback_used: bool) -> None:
        self.total_sessions += 1
        self.total_processing_time_ms += max(0, int(processing_time_ms))
        if model_id:
            self.model_counts[model_id] = self.model_counts.get(model_id, 0) + 1
            self.model_total_time_ms[model_id] = self.model_total_time_ms.get(model_id, 0) + max(0, int(processing_time_ms))
        if fallback_used:
            self.fallback_count += 1

    def record_feedback(self) -> None:
        self.total_feedback += 1

    def snapshot(self, time_range: str = "day", session_id: str | None = None) -> Dict[str, Any]:
        avg_time = int(self.total_processing_time_ms / self.total_sessions) if self.total_sessions else 0
        # Compute simple per-model avg latencies
        per_model_avg = {m: int(self.model_total_time_ms.get(m, 0) / c) if c else 0 for m, c in self.model_counts.items()}
        # Placeholders for accuracy/cost until we collect these
        return {
            "timeRange": time_range,
            "totalSessions": self.total_sessions,
            "averageAccuracy": 0.0,
            "errorReduction": 0.0,
            "averageProcessingTime": avg_time,
            "modelPerformance": {"counts": self.model_counts, "avgLatencyMs": per_model_avg, "fallbacks": self.fallback_count},
            "costSummary": {},
            "userSatisfaction": 0.0,
        }


