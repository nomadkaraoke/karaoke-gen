from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Optional


def cleanup_expired(db_path: str, older_than_days: int = 365 * 3) -> int:
    """Cleanup routine placeholder; returns number of deleted rows.

    Note: This placeholder assumes `data` JSON contains an ISO timestamp under
    key `createdAt`. For production, store timestamps as columns.
    """
    threshold = (datetime.utcnow() - timedelta(days=older_than_days)).isoformat()
    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        # Delete sessions and feedback older than threshold by created_at
        cur.execute("DELETE FROM sessions WHERE created_at < ?", (threshold,))
        cur.execute("DELETE FROM feedback WHERE created_at < ?", (threshold,))
        deleted = cur.rowcount
        conn.commit()
        return deleted


