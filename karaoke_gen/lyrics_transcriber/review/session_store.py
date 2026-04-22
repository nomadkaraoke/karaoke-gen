"""Local on-disk persistence for review-session history.

Mirrors the cloud backend's `/api/review/{job_id}/sessions` shape
(backend/api/routes/review.py + backend/models/review_session.py) using
one JSON file per session under
`{cache_dir}/review_sessions/{audio_hash}/{session_id}.json`.

Sessions are keyed by `audio_hash` (MD5 of the input audio file) rather
than the placeholder `local` job_id so that restoring progress works
across `karaoke-gen` re-runs for the same song and is automatically
isolated between songs sharing a single cache_dir.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


class LocalReviewSessionStore:
    """JSON-on-disk store matching the cloud ReviewSession contract.

    Thread-safety: single-writer assumption (the ReviewServer runs one
    uvicorn worker), so no locking. Reads tolerate partial/corrupt files
    by skipping them.
    """

    def __init__(
        self,
        cache_dir: str,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._root = os.path.join(cache_dir, "review_sessions")
        self._logger = logger or logging.getLogger(__name__)

    # --- public API -----------------------------------------------------

    def save(
        self,
        *,
        audio_hash: str,
        correction_data: Dict[str, Any],
        edit_count: int,
        trigger: str,
        summary: Dict[str, Any],
        artist: Optional[str] = None,
        title: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Persist a session snapshot.

        Returns `{status: 'success', session_id, created_at}` on write, or
        `{status: 'skipped', reason: 'identical_data'}` when the payload
        hash matches the most-recent session for the same `audio_hash`.
        """
        data_hash = self._hash_correction_data(correction_data)

        latest = self._latest_session_meta(audio_hash)
        if latest and latest.get("data_hash") == data_hash:
            return {"status": "skipped", "reason": "identical_data"}

        session_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        envelope: Dict[str, Any] = {
            "session_id": session_id,
            "audio_hash": audio_hash,
            "created_at": now,
            "updated_at": now,
            "edit_count": int(edit_count or 0),
            "trigger": trigger or "auto",
            "artist": artist,
            "title": title,
            "summary": summary or {},
            "data_hash": data_hash,
            "correction_data": correction_data,
        }

        path = self._session_path(audio_hash, session_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(envelope, f)

        return {"status": "success", "session_id": session_id, "created_at": now}

    def list_sessions(self, *, audio_hash: str) -> List[Dict[str, Any]]:
        """Return session metadata (no correction_data), newest first."""
        dir_path = self._song_dir(audio_hash)
        if not os.path.isdir(dir_path):
            return []

        sessions: List[Dict[str, Any]] = []
        for entry in os.listdir(dir_path):
            if not entry.endswith(".json"):
                continue
            envelope = self._read_envelope(os.path.join(dir_path, entry))
            if not envelope:
                continue
            sessions.append(self._meta_from_envelope(envelope))

        sessions.sort(key=lambda s: s.get("created_at") or "", reverse=True)
        return sessions

    def get_session(
        self, *, audio_hash: str, session_id: str
    ) -> Optional[Dict[str, Any]]:
        """Return the full envelope (including correction_data) or None."""
        envelope = self._read_envelope(self._session_path(audio_hash, session_id))
        return envelope

    def delete_session(self, *, audio_hash: str, session_id: str) -> bool:
        """Remove a session file. Returns True on success, False if missing."""
        path = self._session_path(audio_hash, session_id)
        try:
            os.remove(path)
            return True
        except FileNotFoundError:
            return False
        except OSError as e:
            self._logger.warning(f"Failed to delete session {session_id}: {e}")
            return False

    # --- internals ------------------------------------------------------

    def _song_dir(self, audio_hash: str) -> str:
        return os.path.join(self._root, audio_hash)

    def _session_path(self, audio_hash: str, session_id: str) -> str:
        return os.path.join(self._song_dir(audio_hash), f"{session_id}.json")

    def _read_envelope(self, path: str) -> Optional[Dict[str, Any]]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        except OSError as e:
            self._logger.warning(f"Failed to read session file {path}: {e}")
            return None

    @staticmethod
    def _meta_from_envelope(envelope: Dict[str, Any]) -> Dict[str, Any]:
        return {
            k: v for k, v in envelope.items() if k != "correction_data"
        }

    def _latest_session_meta(self, audio_hash: str) -> Optional[Dict[str, Any]]:
        sessions = self.list_sessions(audio_hash=audio_hash)
        return sessions[0] if sessions else None

    @staticmethod
    def _hash_correction_data(correction_data: Dict[str, Any]) -> str:
        payload = json.dumps(correction_data, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
