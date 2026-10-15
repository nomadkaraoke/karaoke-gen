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
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from google.cloud import firestore  # noqa: F401  (tests patch kps.firestore.transactional)
from google.cloud.firestore_v1 import FieldFilter

from backend.models.user import CreditTransaction
from backend.services.login_code_store import (  # noqa: F401  (re-exported for callers/tests)
    CODE_EXPIRY_MINUTES,
    CODE_LENGTH,
    MAX_VERIFY_ATTEMPTS,
    TRANSACTION_MAX_ATTEMPTS,
    CodeThrottled,
    IssuedCode,
    LoginCodeStore,
    TransactionContention,
    VerifyOutcome,
    _aware,
    _now,
    code_doc_id,
    run_firestore_transaction,
)

logger = logging.getLogger(__name__)

LOGIN_CODES_COLLECTION = "kjbox_login_codes"
LOGIN_CODE_USES_COLLECTION = "kjbox_login_code_uses"
SIGNUPS_COLLECTION = "kjbox_signups"
SHOW_CREDITS_COLLECTION = "kjbox_show_credits"
SHOW_CREDIT_USERS_COLLECTION = "kjbox_show_credit_users"

SHOW_CREDIT_REASON = "kjbox show credit"

_DAY = timedelta(hours=24)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ShowCreditCapReached(Exception):
    """Per-user rolling-24h show-credit cap hit."""


@dataclass
class ShowCreditResult:
    granted: bool
    credits: int


class KjboxPartnerService:
    """Code issuance/verification, signup cap, and show-credit bookkeeping.

    Code issue/verify is delegated to :class:`LoginCodeStore` (shared with gen's
    own email-code sign-in) over the kjbox-specific collections.
    """

    def __init__(self, db, pepper: str, transaction_runner=None):
        self.db = db
        # Injectable for unit tests (in-memory Firestore fake); production runs
        # ``fn(transaction)`` under @firestore.transactional (retried on contention).
        self._transaction_runner = transaction_runner
        # The partner secret doubles as the HMAC pepper. The store routes its
        # transactions through self._run_transaction (looked up per call, so a
        # class-level patch in tests applies to code issuance too).
        self._codes = LoginCodeStore(
            db,
            pepper,
            LOGIN_CODES_COLLECTION,
            LOGIN_CODE_USES_COLLECTION,
            transaction_runner=lambda fn: self._run_transaction(fn),
        )

    def _run_transaction(self, fn):
        if self._transaction_runner is not None:
            return self._transaction_runner(fn)
        return run_firestore_transaction(self.db, fn)

    # ------------------------------------------------------------------ codes

    def _hash_code(self, code_id: str, code: str) -> str:
        return self._codes.hash_code(code_id, code)

    def _code_ref(self, email: str):
        return self._codes.code_ref(email)

    def get_code_record(self, email: str) -> Optional[dict]:
        return self._codes.get_code_record(email)

    def recent_send_times(self, record: Optional[dict], now: Optional[datetime] = None) -> list:
        return self._codes.recent_send_times(record, now)

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
        """Mint a fresh code for ``email`` (see :meth:`LoginCodeStore.issue_code`).

        Venue/locale are kept on the record for attribution when the email verifies.
        Raises :class:`CodeThrottled` past ``max_per_hour`` sends.
        """
        return self._codes.issue_code(
            email,
            max_per_hour=max_per_hour,
            needs_credit_eval=needs_credit_eval,
            fields={
                "locale": locale,
                "ui_locale": ui_locale,
                "venue": venue,
                "ip_address": ip_address,
                "user_agent": user_agent,
            },
        )

    def verify_code(self, email: str, code: str) -> tuple[str, Optional[dict]]:
        """Check ``code`` for ``email``. Returns (outcome, code_record)."""
        return self._codes.verify_code(email, code)

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
