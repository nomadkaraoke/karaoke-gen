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
- ``kjbox_signups``          one doc per NEW gen account created via kjbox — written
                             only once the email is VERIFIED (the partner-wide
                             rolling-24h signup cap counts these).
- ``kjbox_show_credits``     one doc per idempotency key that actually GRANTED a
                             credit (id = sha256(key)).
- ``kjbox_show_credit_users`` per-user doc (id = sha256(email)) with recent grant
                             timestamps; read+written in the grant transaction so
                             the per-user rolling-24h cap can't be raced.

Throttle/cap/idempotency decisions run inside Firestore transactions so
concurrent requests can't slip past them.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from google.api_core import exceptions as google_exceptions
from google.cloud import firestore
from google.cloud.firestore_v1 import FieldFilter, Increment

from backend.models.user import CreditTransaction

logger = logging.getLogger(__name__)

LOGIN_CODES_COLLECTION = "kjbox_login_codes"
LOGIN_CODE_USES_COLLECTION = "kjbox_login_code_uses"
SIGNUPS_COLLECTION = "kjbox_signups"
SHOW_CREDITS_COLLECTION = "kjbox_show_credits"
SHOW_CREDIT_USERS_COLLECTION = "kjbox_show_credit_users"

CODE_LENGTH = 6
CODE_EXPIRY_MINUTES = 10
MAX_VERIFY_ATTEMPTS = 5
SHOW_CREDIT_REASON = "kjbox show credit"

TRANSACTION_MAX_ATTEMPTS = 10

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


class TransactionContention(Exception):
    """A Firestore transaction gave up after repeated contention (nothing written)."""


class CodeThrottled(Exception):
    """Per-email code throttle hit (raised out of the issue transaction)."""


class ShowCreditCapReached(Exception):
    """Per-user rolling-24h show-credit cap hit."""


@dataclass
class ShowCreditResult:
    granted: bool
    credits: int


class VerifyOutcome:
    OK = "ok"
    EXPIRED = "expired"
    INVALID = "invalid_code"
    TOO_MANY_ATTEMPTS = "too_many_attempts"


class KjboxPartnerService:
    """Code issuance/verification, signup cap, and show-credit bookkeeping."""

    def __init__(self, db, pepper: str, transaction_runner=None):
        self.db = db
        # The partner secret doubles as the HMAC pepper: a Firestore read alone
        # (backups, exports) can't brute-force the 10^6 code space offline.
        self._pepper = pepper.encode("utf-8")
        # Injectable for unit tests (in-memory Firestore fake); production runs
        # ``fn(transaction)`` under @firestore.transactional (retried on contention).
        self._transaction_runner = transaction_runner

    def _run_transaction(self, fn):
        if self._transaction_runner is not None:
            return self._transaction_runner(fn)
        try:
            return firestore.transactional(fn)(self.db.transaction(max_attempts=TRANSACTION_MAX_ATTEMPTS))
        except ValueError as exc:
            # google-cloud-firestore gives up with ValueError("Failed to commit
            # transaction in N attempts.") under heavy contention. Nothing was
            # written, so callers can safely treat it as "busy, retry".
            if "Failed to commit transaction" in str(exc):
                raise TransactionContention() from exc
            raise

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

    def issue_code(
        self,
        email: str,
        *,
        max_per_hour: int,
        needs_credit_eval: bool,
        locale: Optional[str],
        ui_locale: Optional[str],
        venue: Optional[str],
        ip_address: Optional[str],
        user_agent: Optional[str],
    ) -> IssuedCode:
        """Mint a fresh code for ``email``, replacing (invalidating) any prior one.

        The throttle check, the send_times append and the code write happen in ONE
        transaction, so parallel requests can't exceed ``max_per_hour`` sends
        (raises :class:`CodeThrottled`). Only an HMAC of the code is stored.
        ``merge=True`` keeps any precomputed welcome-credit evaluation across
        re-sends. Venue/locale are kept for attribution when the email verifies.
        """
        email = email.strip().lower()
        ref = self._code_ref(email)

        def _txn(transaction):
            now = _now()
            snap = ref.get(transaction=transaction)
            record = snap.to_dict() if snap.exists else None
            recent = self.recent_send_times(record, now)
            if len(recent) >= max_per_hour:
                raise CodeThrottled()
            code = f"{secrets.randbelow(10 ** CODE_LENGTH):0{CODE_LENGTH}d}"
            code_id = secrets.token_urlsafe(16)
            transaction.set(
                ref,
                {
                    "email": email,
                    "code_id": code_id,
                    "code_hash": self._hash_code(code_id, code),
                    "created_at": now,
                    "expires_at": now + timedelta(minutes=CODE_EXPIRY_MINUTES),
                    "attempts": 0,
                    "send_times": recent + [now],
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

        return self._run_transaction(_txn)

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

    def _show_credit_user_ref(self, email: str):
        return self.db.collection(SHOW_CREDIT_USERS_COLLECTION).document(_sha256(email.strip().lower()))

    def grant_show_credit(
        self,
        user_ref,
        email: str,
        idempotency_key: str,
        *,
        venue: Optional[str],
        only_if_empty: bool,
        max_per_24h: int,
        reason: str = SHOW_CREDIT_REASON,
        max_transactions: int = 100,
    ) -> ShowCreditResult:
        """Atomically: idempotency check, only-if-empty check, per-user cap, key
        claim, and the +1 credit (with its transaction-history entry).

        The key is claimed ONLY when a credit is actually granted, so kjbox can
        call with ``only_if_empty=True`` early and again with the same key and
        ``only_if_empty=False`` later — the second call grants only if the first
        didn't. Raises :class:`ShowCreditCapReached` at the cap.
        """
        email = email.strip().lower()
        key_ref = self._show_credit_ref(idempotency_key)
        counter_ref = self._show_credit_user_ref(email)

        def _txn(transaction):
            now = _now()
            key_snap = key_ref.get(transaction=transaction)
            user_snap = user_ref.get(transaction=transaction)
            counter_snap = counter_ref.get(transaction=transaction)
            user_data = user_snap.to_dict() if user_snap.exists else None
            if user_data is None:
                raise LookupError("user not found")
            credits = int(user_data.get("credits") or 0)

            if key_snap.exists:
                return ShowCreditResult(granted=False, credits=credits)
            if only_if_empty and credits >= 1:
                return ShowCreditResult(granted=False, credits=credits)

            counter = counter_snap.to_dict() if counter_snap.exists else {}
            recent = [t for t in (_aware(x) for x in counter.get("grant_times") or [])
                      if t and now - t < _DAY]
            if len(recent) >= max_per_24h:
                raise ShowCreditCapReached()

            entry = CreditTransaction(
                id=str(uuid.uuid4()), amount=1, reason=reason,
            ).model_dump(mode="json")
            history = list(user_data.get("credit_transactions") or [])
            history = history[-(max_transactions - 1):] + [entry]

            transaction.create(key_ref, {
                "idempotency_key": idempotency_key,
                "email": email,
                "venue": venue,
                "created_at": now,
            })
            transaction.set(counter_ref, {"email": email, "grant_times": recent + [now]})
            transaction.update(user_ref, {
                "credits": credits + 1,
                "credit_transactions": history,
                "updated_at": datetime.utcnow(),
            })
            return ShowCreditResult(granted=True, credits=credits + 1)

        return self._run_transaction(_txn)
