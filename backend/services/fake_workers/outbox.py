"""Captured outbound email for the local stack (the console email provider writes here).

Tests read it through ``GET /api/test/emails`` to follow magic links and codes,
the way production E2E reads a testmail.app inbox.
"""
from __future__ import annotations

import html
import logging
import re
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

OUTBOX_COLLECTION = "local_stack_outbox"

_URL_RE = re.compile(r"https?://[^\s\"'<>]+")
# A 6-digit sign-in code: a standalone run of exactly six digits (not part of a
# hex colour like #123456 or a longer number).
_CODE_RE = re.compile(r"(?<![#\w])(\d{6})(?!\w)")
_STYLE_RE = re.compile(r"<(style|script)[^>]*>.*?</\1>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def _visible_text(text_content: Optional[str], html_content: Optional[str]) -> str:
    if text_content:
        return text_content
    return html.unescape(_TAG_RE.sub(" ", _STYLE_RE.sub(" ", html_content or "")))


def _db():
    from backend.services.firestore_service import FirestoreService

    return FirestoreService().db


def record_email(to_email: str, subject: str, html_content: str, text_content: Optional[str]) -> None:
    text = text_content or ""
    links = list(dict.fromkeys(
        html.unescape(u) for u in _URL_RE.findall(f"{text}\n{html_content or ''}")
    ))
    try:
        _db().collection(OUTBOX_COLLECTION).add({
            "to": (to_email or "").strip().lower(),
            "subject": subject,
            "text": text_content,
            "html": html_content,
            "links": links,
            "codes": list(dict.fromkeys(_CODE_RE.findall(_visible_text(text_content, html_content)))),
            "created_at": datetime.now(timezone.utc),
        })
    except Exception as e:  # capture is best-effort; never break a send
        logger.warning(f"local stack outbox: could not record email to {to_email}: {e}")


def list_emails(to_email: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
    """Newest first. Filtering happens in memory (the outbox is small and test-only)."""
    docs = [d.to_dict() | {"id": d.id} for d in _db().collection(OUTBOX_COLLECTION).stream()]
    if to_email:
        docs = [d for d in docs if d.get("to") == to_email.strip().lower()]
    docs.sort(key=lambda d: d.get("created_at") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    for d in docs:
        if isinstance(d.get("created_at"), datetime):
            d["created_at"] = d["created_at"].isoformat()
    return docs[:limit]


# ------------------------------------------------------------------ Expo push sink
# In-memory (fake workers run in the API process, so no Firestore round-trip is needed).
_push_outbox: List[Dict[str, Any]] = []
_push_lock = threading.Lock()


def record_push(email: str, kind: str, title: str, body: str, url: str) -> None:
    with _push_lock:
        _push_outbox.append({
            "to": (email or "").strip().lower(), "kind": kind, "title": title, "body": body, "url": url,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })


def list_pushes(email: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
    """Newest first."""
    with _push_lock:
        rows = list(_push_outbox)
    if email:
        rows = [r for r in rows if r["to"] == email.strip().lower()]
    return list(reversed(rows))[:limit]


def clear_pushes() -> None:
    with _push_lock:
        _push_outbox.clear()
