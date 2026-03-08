"""
Audio edit session model for persisting input audio editing progress.

Stored as a Firestore subcollection: jobs/{job_id}/audio_edit_sessions/{session_id}
Full edit data is stored in GCS to avoid Firestore's 1MB document limit.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
import uuid


@dataclass
class AudioEditSessionSummary:
    """Compact summary of operations in an audio edit session, for list views."""
    total_operations: int = 0
    operations_breakdown: Dict[str, int] = field(default_factory=dict)
    duration_change_seconds: float = 0.0
    net_duration_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_operations": self.total_operations,
            "operations_breakdown": self.operations_breakdown,
            "duration_change_seconds": self.duration_change_seconds,
            "net_duration_seconds": self.net_duration_seconds,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AudioEditSessionSummary":
        return cls(
            total_operations=data.get("total_operations", 0),
            operations_breakdown=data.get("operations_breakdown", {}),
            duration_change_seconds=data.get("duration_change_seconds", 0.0),
            net_duration_seconds=data.get("net_duration_seconds", 0.0),
        )


@dataclass
class AudioEditSession:
    """
    A saved snapshot of an audio editing session.

    Stored at: jobs/{job_id}/audio_edit_sessions/{session_id}
    Edit data stored at: jobs/{job_id}/audio_edit_sessions/{session_id}.json (GCS)
    """
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    job_id: str = ""
    user_email: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    edit_count: int = 0
    trigger: str = "auto"  # "auto" | "manual" | "submit"
    audio_duration_seconds: Optional[float] = None
    original_duration_seconds: Optional[float] = None
    artist: Optional[str] = None
    title: Optional[str] = None
    summary: AudioEditSessionSummary = field(default_factory=AudioEditSessionSummary)
    edit_data_gcs_path: str = ""

    # Hash of the edit data JSON, used to skip duplicate saves
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
            "original_duration_seconds": self.original_duration_seconds,
            "artist": self.artist,
            "title": self.title,
            "summary": self.summary.to_dict(),
            "edit_data_gcs_path": self.edit_data_gcs_path,
            "data_hash": self.data_hash,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AudioEditSession":
        created_at = data.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at.rstrip("Z")).replace(tzinfo=timezone.utc)

        updated_at = data.get("updated_at")
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at.rstrip("Z")).replace(tzinfo=timezone.utc)

        summary_data = data.get("summary", {})
        summary = AudioEditSessionSummary.from_dict(summary_data) if isinstance(summary_data, dict) else AudioEditSessionSummary()

        return cls(
            session_id=data.get("session_id", str(uuid.uuid4())),
            job_id=data.get("job_id", ""),
            user_email=data.get("user_email", ""),
            created_at=created_at or datetime.now(timezone.utc),
            updated_at=updated_at or datetime.now(timezone.utc),
            edit_count=data.get("edit_count", 0),
            trigger=data.get("trigger", "auto"),
            audio_duration_seconds=data.get("audio_duration_seconds"),
            original_duration_seconds=data.get("original_duration_seconds"),
            artist=data.get("artist"),
            title=data.get("title"),
            summary=summary,
            edit_data_gcs_path=data.get("edit_data_gcs_path", ""),
            data_hash=data.get("data_hash", ""),
        )
