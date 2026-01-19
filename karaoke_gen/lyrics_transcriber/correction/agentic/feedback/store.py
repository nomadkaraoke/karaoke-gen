from __future__ import annotations

import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Dict, Any, Iterable, Optional
from datetime import datetime


class FeedbackStore:
    """SQLite-backed store for sessions, corrections, and feedback.

    This is a minimal implementation to satisfy contract needs; schema may
    evolve. All operations are simple and synchronous for local usage.
    """

    def __init__(self, db_path: str | Path):
        self._db_path = str(db_path)
        self._init()

    def _init(self) -> None:
        with sqlite3.connect(self._db_path) as conn:
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    data TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            # Attempt to add created_at if upgrading from older schema
            try:
                cur.execute("ALTER TABLE sessions ADD COLUMN created_at TEXT")
            except Exception:
                pass
            try:
                cur.execute("ALTER TABLE feedback ADD COLUMN created_at TEXT")
            except Exception:
                pass
            conn.commit()

    def put_session(self, session_id: str, data_json: str) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "REPLACE INTO sessions (id, data, created_at) VALUES (?, ?, ?)",
                (session_id, data_json, datetime.utcnow().isoformat()),
            )
            conn.commit()

    def get_session(self, session_id: str) -> Optional[str]:
        with sqlite3.connect(self._db_path) as conn:
            cur = conn.execute("SELECT data FROM sessions WHERE id = ?", (session_id,))
            row = cur.fetchone()
            return row[0] if row else None

    def put_feedback(self, feedback_id: str, session_id: Optional[str], data_json: str) -> None:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "REPLACE INTO feedback (id, session_id, data, created_at) VALUES (?, ?, ?, ?)",
                (feedback_id, session_id, data_json, datetime.utcnow().isoformat()),
            )
            conn.commit()


