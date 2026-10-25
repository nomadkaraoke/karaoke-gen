"""Reusable emailed sign-in code store (6-digit codes, HMAC-hashed at rest).

Shared by gen's own email-code sign-in (``/api/users/auth/send-code`` +
``verify-code``, collections ``login_codes`` / ``login_code_uses``) and the kjbox
partner API (``KjboxPartnerService``, collections ``kjbox_login_codes`` /
``kjbox_login_code_uses``). Each caller picks its own collections, so the two
flows never share codes or throttle state.

Per collection pair:
- ``<codes_collection>``  one doc per email (id = sha256(email)); holds only an
                          HMAC of the current code, its expiry + attempt counter,
                          the recent send timestamps (per-email throttle), caller
                          metadata (locale, IP, ...) and the precomputed
                          welcome-credit evaluation.
- ``<uses_collection>``   create()-only markers keyed by code_id — makes a code
                          strictly single-use even under concurrent verifies.
- ``<ip_collection>``     optional; one doc per sha256(client IP) with recent send
                          timestamps (per-IP throttle). Only touched when the
                          caller passes ``throttle_ip``.

Throttle decisions run inside ONE Firestore transaction together with the code
write, so concurrent sends can't slip past either limit.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from google.api_core import exceptions as google_exceptions
from google.cloud import firestore
from google.cloud.firestore_v1 import Increment

CODE_LENGTH = 6
CODE_EXPIRY_MINUTES = 10
MAX_VERIFY_ATTEMPTS = 5
TRANSACTION_MAX_ATTEMPTS = 10

THROTTLE_WINDOW = timedelta(hours=1)


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


def recent_times(values, now: Optional[datetime] = None, window: timedelta = THROTTLE_WINDOW) -> list:
    """Timestamps from ``values`` that fall inside the rolling ``window``."""
    now = now or _now()
    times = [_aware(t) for t in values or []]
    return [t for t in times if t and now - t < window]


@dataclass
class IssuedCode:
    code: str
    doc_id: str
    needs_credit_eval: bool


class TransactionContention(Exception):
    """A Firestore transaction gave up after repeated contention (nothing written)."""


class CodeThrottled(Exception):
    """Per-email (or per-IP) code throttle hit (raised out of the issue transaction)."""


class VerifyOutcome:
    OK = "ok"
    EXPIRED = "expired"
    INVALID = "invalid_code"
    TOO_MANY_ATTEMPTS = "too_many_attempts"


def run_firestore_transaction(db, fn: Callable):
    """Run ``fn(transaction)`` under @firestore.transactional (retried on contention).

    google-cloud-firestore gives up with ValueError("Failed to commit transaction
    in N attempts.") under heavy contention. Nothing was written, so it's mapped
    to :class:`TransactionContention` for callers to treat as "busy, retry".
    """
    try:
        return firestore.transactional(fn)(db.transaction(max_attempts=TRANSACTION_MAX_ATTEMPTS))
    except ValueError as exc:
        if "Failed to commit transaction" in str(exc):
            raise TransactionContention() from exc
        raise


class LoginCodeStore:
    """Issue + verify emailed 6-digit sign-in codes in a given pair of collections."""

    def __init__(
        self,
        db,
        pepper: str,
        codes_collection: str,
        uses_collection: str,
        transaction_runner: Optional[Callable] = None,
        ip_collection: Optional[str] = None,
    ):
        self.db = db
        # A server-side secret pepper: a Firestore read alone (backups, exports)
        # can't brute-force the 10^6 code space offline.
        self._pepper = pepper.encode("utf-8")
        self.codes_collection = codes_collection
        self.uses_collection = uses_collection
        self.ip_collection = ip_collection
        # Injectable for unit tests (in-memory Firestore fake) and so callers
        # (KjboxPartnerService) can route through their own runner.
        self._transaction_runner = transaction_runner

    def _run_transaction(self, fn):
        if self._transaction_runner is not None:
            return self._transaction_runner(fn)
        return run_firestore_transaction(self.db, fn)

    def hash_code(self, code_id: str, code: str) -> str:
        return hmac.new(self._pepper, f"{code_id}:{code}".encode("utf-8"), hashlib.sha256).hexdigest()

    def code_ref(self, email: str):
        return self.db.collection(self.codes_collection).document(code_doc_id(email))

    def _ip_ref(self, ip_address: str):
        return self.db.collection(self.ip_collection).document(_sha256(ip_address))

    def get_code_record(self, email: str) -> Optional[dict]:
        snap = self.code_ref(email).get()
        return snap.to_dict() if snap.exists else None

    def recent_send_times(self, record: Optional[dict], now: Optional[datetime] = None) -> list:
        return recent_times((record or {}).get("send_times"), now)

    def issue_code(
        self,
        email: str,
        *,
        max_per_hour: int,
        needs_credit_eval: bool,
        fields: Optional[dict] = None,
        throttle_ip: Optional[str] = None,
        max_per_ip_per_hour: Optional[int] = None,
    ) -> IssuedCode:
        """Mint a fresh code for ``email``, replacing (invalidating) any prior one.

        The throttle checks, the send_times appends and the code write happen in
        ONE transaction, so parallel requests can't exceed ``max_per_hour`` sends
        per email (nor ``max_per_ip_per_hour`` per ``throttle_ip`` when given) —
        raises :class:`CodeThrottled`. A throttled request records nothing.
        Only an HMAC of the code is stored. ``merge=True`` keeps any precomputed
        welcome-credit evaluation across re-sends. ``fields`` (locale, IP, venue,
        tenant, ...) are stored on the record and returned by verify_code.
        """
        email = email.strip().lower()
        ref = self.code_ref(email)
        ip_ref = None
        if throttle_ip and max_per_ip_per_hour is not None:
            if not self.ip_collection:
                raise ValueError("throttle_ip requires ip_collection")
            ip_ref = self._ip_ref(throttle_ip)

        def _txn(transaction):
            now = _now()
            # All reads before any write (Firestore transaction rule).
            snap = ref.get(transaction=transaction)
            ip_snap = ip_ref.get(transaction=transaction) if ip_ref is not None else None
            record = snap.to_dict() if snap.exists else None
            recent = self.recent_send_times(record, now)
            if len(recent) >= max_per_hour:
                raise CodeThrottled()
            ip_recent = None
            if ip_snap is not None:
                ip_recent = recent_times((ip_snap.to_dict() or {}).get("send_times") if ip_snap.exists else None, now)
                if len(ip_recent) >= max_per_ip_per_hour:
                    raise CodeThrottled()

            code = f"{secrets.randbelow(10 ** CODE_LENGTH):0{CODE_LENGTH}d}"
            code_id = secrets.token_urlsafe(16)
            transaction.set(
                ref,
                {
                    **(fields or {}),
                    "email": email,
                    "code_id": code_id,
                    "code_hash": self.hash_code(code_id, code),
                    "created_at": now,
                    "expires_at": now + timedelta(minutes=CODE_EXPIRY_MINUTES),
                    "attempts": 0,
                    "send_times": recent + [now],
                },
                merge=True,
            )
            if ip_ref is not None:
                # Only the IP hash is the doc id; the raw IP isn't stored here.
                transaction.set(ip_ref, {"send_times": ip_recent + [now], "updated_at": now})
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
        ref = self.code_ref(email)
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
        provided = self.hash_code(record["code_id"], candidate)
        if not (candidate.isdigit() and len(candidate) == CODE_LENGTH
                and hmac.compare_digest(provided, expected)):
            return VerifyOutcome.INVALID, None

        try:
            self.db.collection(self.uses_collection).document(record["code_id"]).create(
                {"email": email, "used_at": _now()}
            )
        except google_exceptions.AlreadyExists:
            return VerifyOutcome.EXPIRED, None

        ref.update({"code_hash": None, "used_at": _now()})
        return VerifyOutcome.OK, after

    def verify_fixed_code(self, email: str, code: str, fixed_code: str) -> tuple[str, Optional[dict]]:
        """Verify ``code`` against a fixed, operator-configured code (App Review demo).

        Same attempt accounting as :meth:`verify_code` (atomic increment BEFORE the
        comparison, lockout above MAX_VERIFY_ATTEMPTS) on the same per-email record,
        but the code is reusable (reviewers sign in repeatedly): a success resets the
        counter. A locked-out record stays locked until a new code is issued
        (``issue_code`` resets attempts). Returns (outcome, record-or-{}).
        """
        email = email.strip().lower()
        ref = self.code_ref(email)
        if not ref.get().exists:
            ref.set({"email": email, "attempts": 0}, merge=True)
        ref.update({"attempts": Increment(1)})
        after = ref.get().to_dict() or {}
        if int(after.get("attempts") or 0) > MAX_VERIFY_ATTEMPTS:
            return VerifyOutcome.TOO_MANY_ATTEMPTS, None
        candidate = (code or "").strip()
        if not (fixed_code and hmac.compare_digest(candidate.encode("utf-8"), fixed_code.encode("utf-8"))):
            return VerifyOutcome.INVALID, None
        ref.update({"attempts": 0})
        return VerifyOutcome.OK, after
