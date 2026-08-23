"""Postmark admin service — email history for the admin user-detail view.

Surfaces "every email ever sent to a user" by merging two sources:

1. **Postmark Messages API** (``GET /messages/outbound``) — authoritative for the
   last ~45 days, with the exact rendered HTML and rich delivery metadata
   (delivered / opened / clicked / bounced).
2. **Our Firestore ``email_log``** — every send persisted at send time (see
   ``EmailService._record_sent_email``), so history survives beyond Postmark's
   45-day content retention.

The same per-server ``POSTMARK_SERVER_TOKEN`` used for sending is sufficient to
read outbound message history (no separate Account API token needed).
"""
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from google.cloud import firestore
from google.cloud.firestore_v1 import FieldFilter

logger = logging.getLogger(__name__)

POSTMARK_API_BASE = "https://api.postmarkapp.com"
EMAIL_LOG_COLLECTION = "email_log"

# Postmark requires count+offset on the outbound list; cap what we surface.
_LIST_COUNT = 100
_HTTP_TIMEOUT = 12


def _iso(value: Any) -> Optional[str]:
    """Normalize a timestamp (datetime or str) to an ISO-8601 string."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


class PostmarkAdminService:
    """Read-only email history queries for the admin UI."""

    def __init__(self, server_token: Optional[str] = None):
        self.server_token = server_token if server_token is not None else os.getenv("POSTMARK_SERVER_TOKEN")
        self._db: Optional[firestore.Client] = None

    # -- infra -------------------------------------------------------------
    @property
    def db(self) -> firestore.Client:
        # Lazy so the service can be constructed (and Postmark-only paths used)
        # without a Firestore client in contexts that don't need one.
        if self._db is None:
            self._db = firestore.Client()
        return self._db

    def _postmark_headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/json",
            "X-Postmark-Server-Token": self.server_token or "",
        }

    # -- Postmark queries --------------------------------------------------
    def _fetch_postmark_messages(self, email: str) -> List[Dict[str, Any]]:
        """List outbound Postmark messages sent to ``email`` (best-effort)."""
        if not self.server_token:
            return []
        try:
            resp = requests.get(
                f"{POSTMARK_API_BASE}/messages/outbound",
                headers=self._postmark_headers(),
                params={"recipient": email, "count": _LIST_COUNT, "offset": 0},
                timeout=_HTTP_TIMEOUT,
            )
            if resp.status_code != 200:
                logger.warning(
                    "Postmark outbound list returned %s for recipient=%s: %s",
                    resp.status_code, email, resp.text[:200],
                )
                return []
            messages = resp.json().get("Messages", []) or []
        except (requests.RequestException, ValueError):
            logger.exception("Failed to fetch Postmark outbound messages for %s", email)
            return []

        summaries = []
        for m in messages:
            summaries.append({
                "message_id": m.get("MessageID"),
                "source": "postmark",
                "subject": m.get("Subject"),
                "to": ", ".join(m.get("Recipients") or []) or email,
                "from_email": m.get("From"),
                "sent_at": _iso(m.get("ReceivedAt")),
                "status": m.get("Status"),
                # Postmark tags map to our email_type when we set them; not used yet.
                "email_type": (m.get("Metadata") or {}).get("email_type"),
                "has_stored_html": False,
            })
        return summaries

    def _fetch_log_docs(self, email: str) -> List[Dict[str, Any]]:
        """Read persisted email_log docs for ``email`` (best-effort)."""
        recipient = (email or "").strip().lower()
        try:
            query = (
                self.db.collection(EMAIL_LOG_COLLECTION)
                .where(filter=FieldFilter("recipient", "==", recipient))
                .order_by("created_at", direction=firestore.Query.DESCENDING)
                .limit(_LIST_COUNT)
            )
            docs = list(query.stream())
        except Exception:
            # Missing composite index or emulator quirk — degrade gracefully.
            logger.exception("Failed to read email_log for %s", email)
            return []

        out = []
        for doc in docs:
            data = doc.to_dict() or {}
            out.append({
                "doc_id": doc.id,
                "message_id": data.get("postmark_message_id"),
                "source": "log",
                "subject": data.get("subject"),
                "to": data.get("recipient_raw") or data.get("recipient"),
                "from_email": data.get("from_email"),
                "sent_at": _iso(data.get("created_at")),
                "status": None,
                "email_type": data.get("email_type"),
                "has_stored_html": bool(data.get("html_content")),
            })
        return out

    # -- public API --------------------------------------------------------
    def get_user_email_history(self, email: str) -> Dict[str, Any]:
        """Merged, de-duplicated list of emails sent to ``email`` (newest first)."""
        postmark = self._fetch_postmark_messages(email)
        logged = self._fetch_log_docs(email)

        # Postmark wins for a message present in both (richer status); note that we
        # also have stored HTML so the UI can fall back after the 45-day window.
        by_id: Dict[str, Dict[str, Any]] = {}
        logged_by_mid: Dict[str, Dict[str, Any]] = {
            d["message_id"]: d for d in logged if d.get("message_id")
        }
        for pm in postmark:
            mid = pm.get("message_id")
            if mid and mid in logged_by_mid:
                pm["has_stored_html"] = True
            if mid:
                by_id[mid] = pm

        merged = list(by_id.values())
        for d in logged:
            mid = d.get("message_id")
            if mid and mid in by_id:
                continue  # already represented by the Postmark summary
            merged.append(d)

        merged.sort(key=lambda x: x.get("sent_at") or "", reverse=True)
        return {
            "email": email,
            "count": len(merged),
            "postmark_available": bool(self.server_token),
            "emails": merged,
        }

    def _log_doc_to_detail(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "message_id": data.get("postmark_message_id"),
            "source": "log",
            "subject": data.get("subject"),
            "from_email": data.get("from_email"),
            "to": data.get("recipient_raw") or data.get("recipient"),
            "cc": data.get("cc") or [],
            "bcc": data.get("bcc") or [],
            "sent_at": _iso(data.get("created_at")),
            "email_type": data.get("email_type"),
            "message_stream": data.get("message_stream"),
            "html_body": data.get("html_content"),
            "text_body": data.get("text_content"),
            "status": None,
            "events": [],
        }

    def _fetch_log_detail_by_message_id(self, message_id: str) -> Optional[Dict[str, Any]]:
        try:
            snap = self.db.collection(EMAIL_LOG_COLLECTION).document(message_id).get()
            if snap.exists:
                return self._log_doc_to_detail(snap.to_dict() or {})
        except Exception:
            logger.exception("email_log fallback lookup failed for %s", message_id)
        return None

    def get_email_detail(self, message_id: str, source: str = "postmark") -> Optional[Dict[str, Any]]:
        """Full detail for one email — rendered HTML + delivery metadata.

        ``source="log"`` reads our Firestore record directly (for messages older
        than Postmark's retention). Otherwise we query Postmark and fall back to
        the Firestore log on 404/unavailable.
        """
        if source == "log":
            return self._fetch_log_detail_by_message_id(message_id)

        if self.server_token:
            try:
                resp = requests.get(
                    f"{POSTMARK_API_BASE}/messages/outbound/{message_id}/details",
                    headers=self._postmark_headers(),
                    timeout=_HTTP_TIMEOUT,
                )
                if resp.status_code == 200:
                    return self._postmark_detail(resp.json())
                logger.info(
                    "Postmark message %s details returned %s; trying stored log",
                    message_id, resp.status_code,
                )
            except (requests.RequestException, ValueError):
                logger.exception("Failed to fetch Postmark details for %s", message_id)

        # Fallback: our persisted copy (older than 45 days, or Postmark down).
        return self._fetch_log_detail_by_message_id(message_id)

    def _postmark_detail(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Shape a Postmark message-details payload for the admin modal."""
        events = []
        opened = 0
        clicked = 0
        bounced_detail = None
        delivered_at = None
        for ev in data.get("MessageEvents", []) or []:
            etype = ev.get("Type")
            events.append({
                "type": etype,
                "received_at": _iso(ev.get("ReceivedAt")),
                "details": ev.get("Details") or {},
            })
            if etype == "Delivered":
                delivered_at = _iso(ev.get("ReceivedAt"))
            elif etype == "Opened":
                opened += 1
            elif etype in ("LinkClicked", "Clicked"):
                clicked += 1
            elif etype == "Bounced":
                bounced_detail = ev.get("Details") or {}

        # Derive a coarse status from events when present.
        status = data.get("Status")
        if bounced_detail:
            status = "Bounced"
        elif delivered_at:
            status = "Delivered"

        return {
            "message_id": data.get("MessageID"),
            "source": "postmark",
            "subject": data.get("Subject"),
            "from_email": data.get("From"),
            "to": ", ".join(data.get("Recipients") or []) or data.get("To"),
            "cc": data.get("Cc"),
            "bcc": data.get("Bcc"),
            "sent_at": _iso(data.get("ReceivedAt")),
            "message_stream": data.get("MessageStream"),
            "html_body": data.get("HtmlBody"),
            "text_body": data.get("TextBody"),
            "status": status,
            "delivered_at": delivered_at,
            "open_count": opened,
            "click_count": clicked,
            "bounce": bounced_detail,
            "events": events,
        }


_postmark_admin_service: Optional[PostmarkAdminService] = None


def get_postmark_admin_service() -> PostmarkAdminService:
    """Get the global Postmark admin service instance."""
    global _postmark_admin_service
    if _postmark_admin_service is None:
        _postmark_admin_service = PostmarkAdminService()
    return _postmark_admin_service
