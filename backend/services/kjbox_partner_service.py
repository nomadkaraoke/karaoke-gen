"""Firestore state for the kjbox partner API (``/api/kjbox/*``).

kjbox (the KJ device app at live karaoke nights) signs singers in to gen with a
6-digit emailed code and tops up one "free at the show" credit per make-it job.
This module owns the persistence + policy for that; the HTTP layer lives in
``backend/api/routes/kjbox.py``.

Collections:
- ``kjbox_login_codes``      one doc per email (id = sha256(email)); holds only an
                             HMAC of the current code, its expiry + attempt counter,
                             the recent send timestamps (per-email throttle) and the
                             precomputed welcome-credit evaluation.
- ``kjbox_login_code_uses``  create()-only markers keyed by code_id — makes a code
                             strictly single-use even under concurrent verifies.
- ``kjbox_signups``          one doc per NEW gen account created via kjbox (the
                             partner-wide rolling-24h signup cap counts these).
- ``kjbox_show_credits``     one doc per processed idempotency key (id =
                             sha256(key)); create() makes grants idempotent and the
                             per-user rolling-24h cap counts these.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from google.api_core import exceptions as google_exceptions
from google.cloud.firestore_v1 import FieldFilter, Increment

logger = logging.getLogger(__name__)

LOGIN_CODES_COLLECTION = "kjbox_login_codes"
LOGIN_CODE_USES_COLLECTION = "kjbox_login_code_uses"
SIGNUPS_COLLECTION = "kjbox_signups"
SHOW_CREDITS_COLLECTION = "kjbox_show_credits"

CODE_LENGTH = 6
CODE_EXPIRY_MINUTES = 10
MAX_VERIFY_ATTEMPTS = 5
SHOW_CREDIT_REASON = "kjbox show credit"

_THROTTLE_WINDOW = timedelta(hours=1)
_DAY = timedelta(hours=24)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Any) -> Optional[datetime]:
    """Normalize Firestore/ISO timestamps to tz-aware UTC datetimes."""
    if dt is None:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            return None
    if not isinstance(dt, datetime):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def code_doc_id(email: str) -> str:
    return _sha256(email.strip().lower())


@dataclass
class IssuedCode:
    code: str
    doc_id: str
    needs_credit_eval: bool


class VerifyOutcome:
    OK = "ok"
    EXPIRED = "expired"
    INVALID = "invalid_code"
    TOO_MANY_ATTEMPTS = "too_many_attempts"


class KjboxPartnerService:
    """Code issuance/verification, signup cap, and show-credit bookkeeping."""

    def __init__(self, db, pepper: str):
        self.db = db
        # The partner secret doubles as the HMAC pepper: a Firestore read alone
        # (backups, exports) can't brute-force the 10^6 code space offline.
        self._pepper = pepper.encode("utf-8")

    # ------------------------------------------------------------------ codes

    def _hash_code(self, code_id: str, code: str) -> str:
        return hmac.new(self._pepper, f"{code_id}:{code}".encode("utf-8"), hashlib.sha256).hexdigest()

    def _code_ref(self, email: str):
        return self.db.collection(LOGIN_CODES_COLLECTION).document(code_doc_id(email))

    def get_code_record(self, email: str) -> Optional[dict]:
        snap = self._code_ref(email).get()
        return snap.to_dict() if snap.exists else None

    def recent_send_times(self, record: Optional[dict], now: Optional[datetime] = None) -> list:
        now = now or _now()
        times = [_aware(t) for t in (record or {}).get("send_times") or []]
        return [t for t in times if t and now - t < _THROTTLE_WINDOW]

    def is_email_throttled(self, email: str, max_per_hour: int) -> bool:
        return len(self.recent_send_times(self.get_code_record(email))) >= max_per_hour

    def issue_code(
        self,
        email: str,
        *,
        needs_credit_eval: bool,
        locale: Optional[str],
        ui_locale: Optional[str],
        venue: Optional[str],
        ip_address: Optional[str],
        user_agent: Optional[str],
    ) -> IssuedCode:
        """Mint a fresh code for ``email``, replacing (invalidating) any prior one.

        Only an HMAC of the code is stored. ``merge=True`` keeps the throttle
        history and any precomputed welcome-credit evaluation across re-sends.
        """
        email = email.strip().lower()
        now = _now()
        record = self.get_code_record(email)
        code = f"{secrets.randbelow(10 ** CODE_LENGTH):0{CODE_LENGTH}d}"
        code_id = secrets.token_urlsafe(16)
        send_times = self.recent_send_times(record, now) + [now]

        self._code_ref(email).set(
            {
                "email": email,
                "code_id": code_id,
                "code_hash": self._hash_code(code_id, code),
                "created_at": now,
                "expires_at": now + timedelta(minutes=CODE_EXPIRY_MINUTES),
                "attempts": 0,
                "send_times": send_times,
                "locale": locale,
                "ui_locale": ui_locale,
                "venue": venue,
                "ip_address": ip_address,
                "user_agent": user_agent,
            },
            merge=True,
        )
        has_eval = bool((record or {}).get("credit_eval_decision"))
        return IssuedCode(
            code=code,
            doc_id=code_doc_id(email),
            needs_credit_eval=needs_credit_eval and not has_eval,
        )

    def verify_code(self, email: str, code: str) -> tuple[str, Optional[dict]]:
        """Check ``code`` for ``email``. Returns (outcome, code_record).

        Attempts are counted with an atomic server-side increment BEFORE the
        comparison, so parallel guesses can't exceed MAX_VERIFY_ATTEMPTS. A
        success is claimed via a create()-only marker so a code works once.
        """
        email = email.strip().lower()
        ref = self._code_ref(email)
        snap = ref.get()
        record = snap.to_dict() if snap.exists else None
        if not record or not record.get("code_hash") or not record.get("code_id"):
            return VerifyOutcome.EXPIRED, None

        expires_at = _aware(record.get("expires_at"))
        if not expires_at or _now() > expires_at:
            ref.update({"code_hash": None})
            return VerifyOutcome.EXPIRED, None

        ref.update({"attempts": Increment(1)})
        after = ref.get().to_dict() or {}
        if after.get("code_id") != record["code_id"] or not after.get("code_hash"):
            # Superseded or burned between our read and the increment.
            return VerifyOutcome.EXPIRED, None
        if int(after.get("attempts") or 0) > MAX_VERIFY_ATTEMPTS:
            ref.update({"code_hash": None})
            return VerifyOutcome.TOO_MANY_ATTEMPTS, None

        candidate = (code or "").strip()
        expected = record["code_hash"]
        provided = self._hash_code(record["code_id"], candidate)
        if not (candidate.isdigit() and len(candidate) == CODE_LENGTH
                and hmac.compare_digest(provided, expected)):
            return VerifyOutcome.INVALID, None

        try:
            self.db.collection(LOGIN_CODE_USES_COLLECTION).document(record["code_id"]).create(
                {"email": email, "used_at": _now()}
            )
        except google_exceptions.AlreadyExists:
            return VerifyOutcome.EXPIRED, None

        ref.update({"code_hash": None, "used_at": _now()})
        return VerifyOutcome.OK, after

    # ----------------------------------------------------------- signup cap

    def count_recent_signups(self) -> int:
        cutoff = _now() - _DAY
        query = self.db.collection(SIGNUPS_COLLECTION).where(
            filter=FieldFilter("created_at", ">=", cutoff)
        )
        return sum(1 for _ in query.stream())

    def record_signup(self, email: str, venue: Optional[str]) -> None:
        self.db.collection(SIGNUPS_COLLECTION).document().set(
            {"email": email.strip().lower(), "venue": venue, "created_at": _now()}
        )

    # --------------------------------------------------------- show credits

    def _show_credit_ref(self, idempotency_key: str):
        return self.db.collection(SHOW_CREDITS_COLLECTION).document(_sha256(idempotency_key))

    def show_credit_already_processed(self, idempotency_key: str) -> bool:
        return self._show_credit_ref(idempotency_key).get().exists

    def count_recent_show_credits(self, email: str) -> int:
        # Equality-only query (no composite index needed); a user has few docs,
        # so the rolling-window filter happens here.
        cutoff = _now() - _DAY
        query = self.db.collection(SHOW_CREDITS_COLLECTION).where(
            filter=FieldFilter("email", "==", email.strip().lower())
        )
        count = 0
        for snap in query.stream():
            created = _aware((snap.to_dict() or {}).get("created_at"))
            if created and created >= cutoff:
                count += 1
        return count

    def claim_show_credit(self, idempotency_key: str, email: str, venue: Optional[str]) -> bool:
        """Atomically claim ``idempotency_key``. False if it was already processed."""
        try:
            self._show_credit_ref(idempotency_key).create(
                {
                    "idempotency_key": idempotency_key,
                    "email": email.strip().lower(),
                    "venue": venue,
                    "created_at": _now(),
                }
            )
            return True
        except google_exceptions.AlreadyExists:
            return False

    def release_show_credit(self, idempotency_key: str) -> None:
        """Undo a claim whose credit grant failed, so kjbox can retry the key."""
        try:
            self._show_credit_ref(idempotency_key).delete()
        except Exception:  # noqa: BLE001
            logger.exception("kjbox: failed to release show-credit claim")
