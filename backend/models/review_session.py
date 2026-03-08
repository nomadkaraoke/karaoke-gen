"""
Review session model for persisting lyrics review progress.

Stored as a Firestore subcollection: jobs/{job_id}/review_sessions/{session_id}
Full correction data is stored in GCS to avoid Firestore's 1MB document limit.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
import uuid


@dataclass
class ReviewSessionSummary:
    """Compact summary of edits in a review session, for list views."""
    total_segments: int = 0
    total_words: int = 0
    corrections_made: int = 0
    changed_words: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_segments": self.total_segments,
            "total_words": self.total_words,
            "corrections_made": self.corrections_made,
            "changed_words": self.changed_words,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReviewSessionSummary":
        return cls(
            total_segments=data.get("total_segments", 0),
            total_words=data.get("total_words", 0),
            corrections_made=data.get("corrections_made", 0),
            changed_words=data.get("changed_words", []),
        )


@dataclass
class ReviewSession:
    """
    A saved snapshot of a lyrics review editing session.

    Stored at: jobs/{job_id}/review_sessions/{session_id}
    Correction data stored at: jobs/{job_id}/review_sessions/{session_id}.json (GCS)
    """
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    job_id: str = ""
    user_email: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    edit_count: int = 0
    trigger: str = "auto"  # "auto" | "preview" | "manual"
    audio_duration_seconds: Optional[float] = None
    artist: Optional[str] = None
    title: Optional[str] = None
    summary: ReviewSessionSummary = field(default_factory=ReviewSessionSummary)
    correction_data_gcs_path: str = ""

    # Hash of the correction data JSON, used to skip duplicate saves
    data_hash: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "job_id": self.job_id,
            "user_email": self.user_email,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "edit_count": self.edit_count,
            "trigger": self.trigger,
            "audio_duration_seconds": self.audio_duration_seconds,
            "artist": self.artist,
            "title": self.title,
            "summary": self.summary.to_dict(),
            "correction_data_gcs_path": self.correction_data_gcs_path,
            "data_hash": self.data_hash,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReviewSession":
        created_at = data.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.rstrip("Z")).replace(tzinfo=timezone.utc)

        updated_at = data.get("updated_at")
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at.rstrip("Z")).replace(tzinfo=timezone.utc)

        summary_data = data.get("summary", {})
        summary = ReviewSessionSummary.from_dict(summary_data) if isinstance(summary_data, dict) else ReviewSessionSummary()

        return cls(
            session_id=data.get("session_id", str(uuid.uuid4())),
            job_id=data.get("job_id", ""),
            user_email=data.get("user_email", ""),
            created_at=created_at or datetime.now(timezone.utc),
            updated_at=updated_at or datetime.now(timezone.utc),
            edit_count=data.get("edit_count", 0),
            trigger=data.get("trigger", "auto"),
            audio_duration_seconds=data.get("audio_duration_seconds"),
            artist=data.get("artist"),
            title=data.get("title"),
            summary=summary,
            correction_data_gcs_path=data.get("correction_data_gcs_path", ""),
            data_hash=data.get("data_hash", ""),
        )
