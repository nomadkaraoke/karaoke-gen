"""Self-serve account deletion (``DELETE /api/users/me``) and the kjbox deletion queue.

App Store 5.1.1(v) and Google Play require in-app account deletion. Steps run in
a fixed order, each idempotent, and any failure stops the run so the client
retries (503 with the step name):

1. preconditions: refuse (409) admins and accounts that carry money or work
   (a Firestore failure here is a 503 ``preconditions``)
2. cancel the user's active jobs (no refund: unused credits are forfeited)
3. decide: erase the user's decide data (partner API)
4. RevenueCat: delete the v2 customer ``app_user_id`` (skipped without a key)
5. Apple: revoke Sign in with Apple when the app sent an authorization code
   (best-effort: a failed revoke is a WARNING, never a 503)
6. kjbox: enqueue ``account_deletions/{sha256(email)}`` for the box to pull
7. gen inputs + personal data in jobs: delete ``uploads/{id}/**`` and
   ``jobs/{id}/input/**``, revoke the customer's Dropbox share link, strip
   names/emails/IPs from the job doc and set ``user_email = "deleted:{uuid}"``.
   Published outputs (stems, renders, MP4s, the Dropbox folder, YouTube) are
   NEVER deleted.
8. gen records: ``deleted_users/{uuid}`` (no email/name, only ``email_sha256``),
   delete magic links, login codes and email_log entries sent to the user (and
   drop the address from other entries' cc/bcc); anonymise records kept for
   accounting/history (kjbox show credits, the referral link (disabled), vanity
   requests, referral earnings, credit evaluations (signup IP/fingerprint
   removed), IAP purchase/refund markers, song requests and votes); delete
   sessions, then ``gen_users/{email}``

Sessions go last so a run that fails part-way leaves the user signed in to retry.
The ``deletion_id`` (UUID4) is minted on the first attempt and stored on the user
doc, so every retry reuses it. After the final step decide is called a second
time (best-effort) to catch a decide profile a still-valid token recreated
mid-deletion.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote

import httpx
from google.api_core import exceptions as google_exceptions
from google.cloud.firestore_v1 import DELETE_FIELD, FieldFilter

from backend.config import get_settings
from backend.services.decide_partner_client import DecidePartnerClient, DecidePartnerError
from backend.services.kjbox_partner_service import (
    LOGIN_CODES_COLLECTION as KJBOX_LOGIN_CODES_COLLECTION,
    SHOW_CREDIT_USERS_COLLECTION,
    SHOW_CREDITS_COLLECTION,
    SIGNUPS_COLLECTION as KJBOX_SIGNUPS_COLLECTION,
)
from backend.services.login_code_store import TransactionContention, code_doc_id, run_firestore_transaction
from backend.services.referral_service import (
    REFERRAL_EARNINGS_COLLECTION,
    REFERRAL_LINKS_COLLECTION,
    REFERRAL_VANITY_REQUESTS_COLLECTION,
)
from backend.services.user_service import (
    MAGIC_LINKS_COLLECTION,
    SESSIONS_COLLECTION,
    USERS_COLLECTION,
)

logger = logging.getLogger(__name__)

ACCOUNT_DELETIONS_COLLECTION = "account_deletions"
DELETED_USERS_COLLECTION = "deleted_users"
GEN_LOGIN_CODES_COLLECTION = "login_codes"  # backend/api/routes/login_code.py
# Owned by modules too heavy to import here (tests guard these against drift):
CREDIT_EVALUATIONS_COLLECTION = "credit_evaluations"  # credit_evaluation_service
SONG_REQUESTS_COLLECTION = "song_requests"  # song_request_service.REQUESTS_COLLECTION
SONG_REQUEST_VOTES_COLLECTION = "song_request_votes"  # song_request_service.VOTES_COLLECTION
SONG_REQUEST_SUBMISSIONS_COLLECTION = "song_request_submissions"  # song_request_service.SUBMISSIONS_COLLECTION
EMAIL_LOG_COLLECTION = "email_log"  # postmark_admin_service / email_service
PROCESSED_IAP_TRANSACTIONS_COLLECTION = "processed_revenuecat_transactions"  # iap_service

# API v2 (the stored key is a project-scoped v2 secret key). Deletion is queued by RevenueCat.
REVENUECAT_CUSTOMER_URL = "https://api.revenuecat.com/v2/projects/{project_id}/customers/{customer_id}"
_HTTP_TIMEOUT_SECONDS = 20

# Job statuses that are finished; everything else counts as "in progress".
TERMINAL_STATUSES = frozenset({"complete", "prep_complete", "failed", "cancelled", "error"})
# GCS prefixes that hold only what the user supplied (or edits of it), never outputs:
# - uploads/{id}/          signed-URL / multipart uploads, downloaded source audio, lyrics files
# - jobs/{id}/input/       the persisted copy of uploads/ (backend/services/input_persistence.py)
# - jobs/{id}/uploads/     a custom instrumental uploaded in review (routes/jobs.py
#                          _instrumental_source_prefix); its output stems/custom_instrumental.flac stays
# - jobs/{id}/audio_edit/  audio-edit uploads (upload_*), edited input mixes (edit_*), edit log, waveform
# - jobs/{id}/style/       style params and the user's uploaded backgrounds / font
INPUT_PREFIXES = (
    "uploads/{job_id}/",
    "jobs/{job_id}/input/",
    "jobs/{job_id}/uploads/",
    "jobs/{job_id}/audio_edit/",
    "jobs/{job_id}/style/",
)
# Job-root copies of the user's own instrumental (backend/utils/existing_instrumental.py).
STAGED_INSTRUMENTAL_STEMS = ("custom_instrumental", "existing_instrumental")
# Personal keys inside a job's state_data (names, emails, notification addresses).
PERSONAL_STATE_KEYS = ("customer_email", "customer_name", "notification_email", "display_name")

# 409 reason codes (the app and web show "Contact support to close this account").
REFUSAL_CODES = ("admin", "referral_balance", "connect_account", "order_in_progress")


def email_sha256(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def deleted_owner(deletion_id: str) -> str:
    return f"deleted:{deletion_id}"


class AccountNotFound(Exception):
    pass


class AccountDeletionRefused(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class AccountDeletionStepFailed(Exception):
    def __init__(self, step: str):
        super().__init__(step)
        self.step = step


class RevenueCatError(RuntimeError):
    """RevenueCat answered non-2xx (other than 404)."""


def _is_retryable(exc: BaseException) -> bool:
    """A transient remote/platform failure the client's retry is expected to clear."""
    if isinstance(exc, (DecidePartnerError, RevenueCatError, httpx.HTTPError, TransactionContention)):
        return True
    if isinstance(exc, (
        google_exceptions.ServerError,  # 5xx incl. ServiceUnavailable, DeadlineExceeded
        google_exceptions.TooManyRequests,
        google_exceptions.Aborted,
    )):
        return True
    # Dropbox SDK transient errors (imported lazily: the SDK is optional at import time)
    return type(exc).__name__ in ("InternalServerError", "RateLimitError") and type(exc).__module__.startswith("dropbox")


def _log_step_failure(deletion_id: str, step: str, exc: BaseException) -> None:
    """#alerts rule: a retryable failure is a WARNING (the client retries on 503);
    anything unexpected is an ERROR, once per request, naming the step."""
    if _is_retryable(exc):
        logger.warning(
            "Account deletion %s: step %s failed (%s: %s); client will retry",
            deletion_id, step, type(exc).__name__, exc,
        )
    else:
        logger.exception("Account deletion %s failed at step %s; client will retry", deletion_id, step)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return value if isinstance(value, str) else None


def _aware(value: Any) -> Optional[datetime]:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return None


def _default_apple_revoke(code: str) -> bool:
    from backend.services.apple_revoke_service import AppleRevoker

    # Called from a sync (threadpool) route handler, so there is no running loop.
    return asyncio.run(AppleRevoker().revoke(code))


def _default_cancel_job(job_id: str) -> bool:
    from backend.services.job_manager import JobManager

    return JobManager().cancel_job(job_id, reason="account deleted", refund=False)


class AccountDeletionService:
    def __init__(
        self,
        db,
        *,
        jobs_collection: Optional[str] = None,
        cancel_job: Optional[Callable[[str], bool]] = None,
        bucket=None,
        dropbox=None,
        decide: Optional[DecidePartnerClient] = None,
        http_client=None,
        apple_revoke: Optional[Callable[[str], bool]] = None,
        transaction_runner: Optional[Callable] = None,
        settings=None,
    ):
        self.db = db
        self.settings = settings or get_settings()
        self.jobs_collection = jobs_collection or self.settings.firestore_collection
        self._cancel_job = cancel_job or _default_cancel_job
        self._bucket = bucket
        self._dropbox = dropbox
        self._decide = decide
        self._http_client = http_client  # injectable httpx.Client (RevenueCat)
        self._apple_revoke = apple_revoke or _default_apple_revoke
        self._transaction_runner = transaction_runner

    # ------------------------------------------------------------ lazy deps

    @property
    def bucket(self):
        if self._bucket is None:
            from backend.services.storage_service import StorageService

            self._bucket = StorageService().bucket
        return self._bucket

    @property
    def dropbox(self):
        if self._dropbox is None:
            from backend.services.dropbox_service import get_dropbox_service

            self._dropbox = get_dropbox_service()
        return self._dropbox

    @property
    def decide(self) -> DecidePartnerClient:
        if self._decide is None:
            self._decide = DecidePartnerClient()
        return self._decide

    def _run_transaction(self, fn):
        if self._transaction_runner is not None:
            return self._transaction_runner(fn)
        return run_firestore_transaction(self.db, fn)

    # ------------------------------------------------------------- queries

    def _jobs(self):
        return self.db.collection(self.jobs_collection)

    def _owned_jobs(self, email: str) -> List[Any]:
        return list(self._jobs().where(filter=FieldFilter("user_email", "==", email)).stream())

    def _customer_jobs(self, email: str) -> List[Any]:
        """Made-for-you orders: owned by the admin, delivered to ``customer_email``."""
        return list(
            self._jobs()
            .where(filter=FieldFilter("customer_email", "==", email))
            .where(filter=FieldFilter("made_for_you", "==", True))
            .stream()
        )

    @staticmethod
    def _is_active(job: dict) -> bool:
        return str(job.get("status") or "").lower() not in TERMINAL_STATUSES

    def _any(self, query) -> bool:
        return any(True for _ in query.limit(1).stream())

    # ------------------------------------------------------- preconditions

    def refusal_code(self, email: str, user: dict, is_admin: bool = False) -> Optional[str]:
        """The 409 reason this account can't self-delete, or None."""
        if is_admin or str(user.get("role") or "").lower() == "admin":
            return "admin"
        pending_earnings = (
            self.db.collection(REFERRAL_EARNINGS_COLLECTION)
            .where(filter=FieldFilter("referrer_email", "==", email))
            .where(filter=FieldFilter("status", "==", "pending"))
        )
        if self._any(pending_earnings):
            return "referral_balance"
        if user.get("stripe_connect_account_id"):
            return "connect_account"
        if any(self._is_active(snap.to_dict() or {}) for snap in self._customer_jobs(email)):
            return "order_in_progress"
        return None

    def _user_ref(self, email: str):
        """The user's doc: keyed by email, or (older docs) by hash with an ``email`` field."""
        users = self.db.collection(USERS_COLLECTION)
        ref = users.document(email)
        if ref.get().exists:
            return ref
        for snap in users.where(filter=FieldFilter("email", "==", email)).limit(1).stream():
            return snap.reference
        raise AccountNotFound(email)

    def _load_user(self, email: str) -> dict:
        return self._user_ref(email).get().to_dict() or {}

    def preview(self, email: str, is_admin: bool = False) -> dict:
        email = email.strip().lower()
        user = self._load_user(email)
        in_progress = sum(1 for snap in self._owned_jobs(email) if self._is_active(snap.to_dict() or {}))
        return {
            "credits": max(int(user.get("credits") or 0), 0),
            "jobs_in_progress": in_progress,
            "blocked": self.refusal_code(email, user, is_admin=is_admin),
        }

    # -------------------------------------------------------------- delete

    def delete(self, email: str, apple_code: Optional[str] = None, is_admin: bool = False) -> dict:
        """Run every deletion step. Raises AccountNotFound / AccountDeletionRefused /
        AccountDeletionStepFailed; returns a per-step summary on success."""
        email = email.strip().lower()
        try:
            user = self._load_user(email)
            code = self.refusal_code(email, user, is_admin=is_admin)
            if code:
                logger.info("Account deletion refused (%s)", code)
                raise AccountDeletionRefused(code)
            deletion_id = self._ensure_deletion_id(email)
        except (AccountNotFound, AccountDeletionRefused):
            raise
        except Exception as e:
            _log_step_failure("-", "preconditions", e)
            raise AccountDeletionStepFailed("preconditions")

        summary: Dict[str, Any] = {"deletion_id": deletion_id}
        steps = (
            ("jobs", lambda: self._cancel_active_jobs(email)),
            ("decide", lambda: self.decide.delete_user(email).get("deleted", {})),
            ("revenuecat", lambda: self._delete_revenuecat(user.get("app_user_id"))),
            ("apple", lambda: self._revoke_apple(apple_code)),
            ("kjbox", lambda: self._enqueue_kjbox(email, deletion_id)),
            ("files", lambda: self._scrub_jobs(email, deletion_id)),
            ("records", lambda: self._delete_records(email, deletion_id)),
        )
        for step, run in steps:
            try:
                summary[step] = run()
            except Exception as e:
                _log_step_failure(deletion_id, step, e)
                raise AccountDeletionStepFailed(step)

        # A token that was still valid mid-run could have recreated a decide profile.
        try:
            self.decide.delete_user(email)
        except Exception as e:
            logger.warning("Account deletion %s: second decide delete failed (%s)", deletion_id, e)

        logger.info("Account deletion %s complete: %s", deletion_id, summary)
        return summary

    def _ensure_deletion_id(self, email: str) -> str:
        ref = self._user_ref(email)

        def txn(transaction):
            snap = ref.get(transaction=transaction)
            existing = (snap.to_dict() or {}).get("deletion_id")
            if existing:
                return existing
            new_id = str(uuid.uuid4())
            transaction.update(ref, {"deletion_id": new_id, "deletion_started_at": _now()})
            return new_id

        return self._run_transaction(txn)

    # Step 2
    def _cancel_active_jobs(self, email: str) -> int:
        cancelled = 0
        for snap in self._owned_jobs(email):
            if self._is_active(snap.to_dict() or {}) and self._cancel_job(snap.id):
                cancelled += 1
        return cancelled

    # Step 4
    def _delete_revenuecat(self, app_user_id: Optional[str]) -> str:
        key = getattr(self.settings, "revenuecat_secret_api_key", "")
        if not key:
            logger.info("Account deletion: RevenueCat skipped (REVENUECAT_SECRET_API_KEY not set)")
            return "skipped"
        if not app_user_id:
            return "no_app_user"
        project_id = getattr(self.settings, "revenuecat_project_id", "") or "9ea947ce"
        url = REVENUECAT_CUSTOMER_URL.format(
            project_id=quote(project_id, safe=""), customer_id=quote(app_user_id, safe=""))
        headers = {"Authorization": f"Bearer {key}"}
        if self._http_client is not None:
            resp = self._http_client.request("DELETE", url, headers=headers)
        else:
            with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS) as client:
                resp = client.request("DELETE", url, headers=headers)
        if resp.status_code == 404:
            return "absent"
        if not 200 <= resp.status_code < 300:
            raise RevenueCatError(f"RevenueCat delete returned HTTP {resp.status_code}")
        return "deleted"

    # Step 5
    def _revoke_apple(self, code: Optional[str]) -> str:
        if not code:
            return "no_code"
        try:
            revoked = self._apple_revoke(code)
        except Exception as e:  # the revoker never raises, but erasure must not depend on it
            logger.warning("Account deletion: Apple revoke raised %s", type(e).__name__)
            revoked = False
        if not revoked:
            logger.warning("Account deletion: Sign in with Apple token not revoked")
            return "failed"
        return "revoked"

    # Step 6
    def _enqueue_kjbox(self, email: str, deletion_id: str) -> str:
        email_hash = email_sha256(email)
        ref = self.db.collection(ACCOUNT_DELETIONS_COLLECTION).document(email_hash)
        snap = ref.get()
        if snap.exists and (snap.to_dict() or {}).get("deletion_id") == deletion_id:
            return "already_queued"
        # New entry, or an earlier deletion of a re-created account: (re)queue it.
        ref.set({
            "email_hash": email_hash,
            "deletion_id": deletion_id,
            "created_at": _now(),
            "targets": {"kjbox": "pending"},
        })
        return "queued"

    # Step 7
    def _scrub_jobs(self, email: str, deletion_id: str) -> dict:
        totals = {"jobs": 0, "input_objects_deleted": 0, "dropbox_links_revoked": 0}
        seen = set()
        for snap in self._owned_jobs(email) + self._customer_jobs(email):
            if snap.id in seen:
                continue
            seen.add(snap.id)
            result = self._scrub_job(snap, email, deletion_id)
            totals["jobs"] += 1
            totals["input_objects_deleted"] += result["input_objects_deleted"]
            totals["dropbox_links_revoked"] += result["dropbox_link_revoked"]
            logger.info(
                "Account deletion %s: job %s scrubbed (%d input objects deleted, dropbox link revoked: %s)",
                deletion_id, snap.id, result["input_objects_deleted"], result["dropbox_link_revoked"],
            )
        return totals

    def _delete_prefix(self, prefix: str) -> int:
        deleted = 0
        for blob in self.bucket.list_blobs(prefix=prefix):
            try:
                blob.delete()
                deleted += 1
            except google_exceptions.NotFound:
                pass
        return deleted

    def _delete_staged_instrumentals(self, job_id: str) -> int:
        """The user's own instrumental, copied to the job root (``jobs/{id}/custom_instrumental.<ext>``,
        ``jobs/{id}/existing_instrumental.<ext>``; see backend/utils/existing_instrumental.py).
        Only direct children of the job root: ``stems/custom_instrumental.flac`` is an output."""
        root = f"jobs/{job_id}/"
        deleted = 0
        for blob in self.bucket.list_blobs(prefix=root):
            rel = blob.name[len(root):]
            if "/" in rel or rel.rsplit(".", 1)[0] not in STAGED_INSTRUMENTAL_STEMS:
                continue
            try:
                blob.delete()
                deleted += 1
            except google_exceptions.NotFound:
                pass
        return deleted

    def _scrub_job(self, snap, email: str, deletion_id: str) -> dict:
        """Delete one job's inputs, revoke its share link, then anonymise the doc.

        The doc update comes last: until it lands the job still matches the
        user's email, so a retry picks it up again.
        """
        job_id = snap.id
        data = snap.to_dict() or {}
        state = data.get("state_data") or {}

        deleted = sum(self._delete_prefix(prefix.format(job_id=job_id)) for prefix in INPUT_PREFIXES)
        deleted += self._delete_staged_instrumentals(job_id)

        link = state.get("dropbox_link")
        revoked = 0
        if link and self.dropbox.revoke_shared_link(link) is not False:
            revoked = 1

        tomb = deleted_owner(deletion_id)
        updates: Dict[str, Any] = {}
        if data.get("user_email") == email:
            updates["user_email"] = tomb
        if data.get("customer_email") == email:
            updates["customer_email"] = tomb
        if data.get("customer_notes"):
            updates["customer_notes"] = None
        if data.get("creation_ip"):
            updates["creation_ip"] = None
        for key in PERSONAL_STATE_KEYS + ("dropbox_link",):
            if key in state:
                updates[f"state_data.{key}"] = DELETE_FIELD
        if "client_ip" in (data.get("request_metadata") or {}):
            updates["request_metadata.client_ip"] = DELETE_FIELD
        if updates:
            self._jobs().document(job_id).update(updates)
        return {"input_objects_deleted": deleted, "dropbox_link_revoked": revoked}

    # Step 8
    def _delete_where(self, collection: str, field: str, value: str) -> int:
        count = 0
        for snap in self.db.collection(collection).where(filter=FieldFilter(field, "==", value)).stream():
            snap.reference.delete()
            count += 1
        return count

    def _delete_records(self, email: str, deletion_id: str) -> dict:
        user_ref = self._user_ref(email)
        user = user_ref.get().to_dict() or {}

        tomb_ref = self.db.collection(DELETED_USERS_COLLECTION).document(deletion_id)
        if not tomb_ref.get().exists:
            tomb_ref.set({
                "deletion_id": deletion_id,
                "email_sha256": email_sha256(email),
                "credits_forfeited": max(int(user.get("credits") or 0), 0),
                "credits": int(user.get("credits") or 0),
                "credit_transactions": user.get("credit_transactions") or [],
                "total_jobs_created": user.get("total_jobs_created") or 0,
                "total_jobs_completed": user.get("total_jobs_completed") or 0,
                "total_spent": user.get("total_spent") or 0,
                "welcome_credits_granted": bool(user.get("welcome_credits_granted")),
                "created_at": user.get("created_at"),
                "deleted_at": _now(),
            })

        summary = {
            "magic_links": self._delete_where(MAGIC_LINKS_COLLECTION, "email", email),
            "kjbox_signups": self._delete_where(KJBOX_SIGNUPS_COLLECTION, "email", email),
        }
        login_codes = 0
        for collection in (GEN_LOGIN_CODES_COLLECTION, KJBOX_LOGIN_CODES_COLLECTION):
            ref = self.db.collection(collection).document(code_doc_id(email))
            if ref.get().exists:
                ref.delete()
                login_codes += 1
        summary["login_codes"] = login_codes
        summary["anonymised"] = self._anonymise_kept_records(email, deletion_id)
        summary["email_log"] = self._delete_where(EMAIL_LOG_COLLECTION, "recipient", email)
        summary["email_log_copies"] = self._remove_from_email_log_copies(email)
        # Sessions, then the user doc, last: a failure before here leaves the
        # user signed in so the app can retry. (Web Push subscriptions and Expo
        # push tokens live on the user doc and go with it.)
        # Narrow window: if the user-doc delete below fails after the sessions are
        # gone, the app can't retry with its old token, but the gen_users doc (with
        # its deletion_id) is still there, so the user can sign in again and retry,
        # and the retry reuses the same deletion_id.
        summary["sessions"] = self._delete_where(SESSIONS_COLLECTION, "user_email", email)
        user_ref.delete()
        return summary

    def _remove_from_email_log_copies(self, email: str) -> int:
        """Drop the address from cc/bcc of mail sent to someone else (that log entry is theirs)."""
        touched = set()
        for field in ("cc", "bcc"):
            for snap in self.db.collection(EMAIL_LOG_COLLECTION).where(
                    filter=FieldFilter(field, "array_contains", email)).stream():
                kept = [a for a in (snap.to_dict() or {}).get(field) or []
                        if not isinstance(a, str) or a.strip().lower() != email]
                snap.reference.update({field: kept})
                touched.add(snap.id)
        return len(touched)

    def _anonymise_where(self, collection: str, field: str, email: str, tomb: str, extra=None) -> int:
        """Replace ``field == email`` with the tombstone (plus ``extra`` updates) on every match."""
        count = 0
        for snap in self.db.collection(collection).where(filter=FieldFilter(field, "==", email)).stream():
            snap.reference.update({field: tomb, **(extra(snap.to_dict() or {}) if extra else {})})
            count += 1
        return count

    def _move_doc(self, collection: str, snap, new_id: str, updates: dict) -> None:
        """Re-key a doc whose id contains the email (create the new doc first, then delete)."""
        data = snap.to_dict() or {}
        data.update(updates)
        self.db.collection(collection).document(new_id).set(data)
        snap.reference.delete()

    def _anonymise_kept_records(self, email: str, deletion_id: str) -> dict:
        """Records kept for accounting or history, with the email replaced by ``deleted:{id}``.

        Idempotent: every lookup is by the email, which a finished pass has removed.
        """
        tomb = deleted_owner(deletion_id)
        out: Dict[str, int] = {}

        # kjbox show-credit grants: per-singer free-credit accounting (doc ids are hashes).
        out["kjbox_show_credits"] = self._anonymise_where(SHOW_CREDITS_COLLECTION, "email", email, tomb)
        counter = self.db.collection(SHOW_CREDIT_USERS_COLLECTION).document(email_sha256(email))
        snap = counter.get()
        if snap.exists and (snap.to_dict() or {}).get("email") == email:
            counter.update({"email": tomb})
            out["kjbox_show_credit_users"] = 1

        # The user's own referral link: disabled (ReferralLink.enabled) and anonymised.
        out["referral_links"] = self._anonymise_where(
            REFERRAL_LINKS_COLLECTION, "owner_email", email, tomb,
            extra=lambda d: {"enabled": False, "display_name": None, "custom_message": None},
        )

        # Vanity requests are keyed by the owner's email: re-key them.
        moved = 0
        vanity = self.db.collection(REFERRAL_VANITY_REQUESTS_COLLECTION)
        for snap in vanity.where(filter=FieldFilter("owner_email", "==", email)).stream():
            self._move_doc(REFERRAL_VANITY_REQUESTS_COLLECTION, snap, tomb, {"id": tomb, "owner_email": tomb})
            moved += 1
        out["referral_vanity_requests"] = moved

        # Referral earnings stay for payout accounting; the emails go.
        out["referral_earnings"] = sum(
            self._anonymise_where(REFERRAL_EARNINGS_COLLECTION, field, email, tomb)
            for field in ("referred_email", "referrer_email")
        )

        def _scrub_evaluation(d):
            updates = {}
            reasoning = d.get("reasoning")
            if isinstance(reasoning, str) and email in reasoning:
                updates["reasoning"] = reasoning.replace(email, tomb)
            # The signup IP and device fingerprint are personal data; the match counts are not.
            snapshot = d.get("signals_snapshot")
            if isinstance(snapshot, dict):
                for key in ("user_ip", "user_fingerprint"):
                    if key in snapshot:
                        updates[f"signals_snapshot.{key}"] = DELETE_FIELD
            return updates

        out["credit_evaluations"] = self._anonymise_where(
            CREDIT_EVALUATIONS_COLLECTION, "email", email, tomb, extra=_scrub_evaluation)

        # IAP purchase/refund markers keep the owner for refund matching; a refund
        # whose purchase owner is ``deleted:…`` is ignored, never debited from a
        # new account that later signs up with the same email.
        out["iap_transactions"] = self._anonymise_where(
            PROCESSED_IAP_TRANSACTIONS_COLLECTION, "email", email, tomb)

        # Song requests: submitter, current owner and the handoff history.
        requests = self.db.collection(SONG_REQUESTS_COLLECTION)
        touched = set()
        for field in ("submitted_by", "owner_email"):
            for snap in requests.where(filter=FieldFilter(field, "==", email)).stream():
                snap.reference.update({field: tomb})
                touched.add(snap.id)
        for snap in requests.where(filter=FieldFilter("attempted_owners", "array_contains", email)).stream():
            owners = [tomb if o == email else o for o in (snap.to_dict() or {}).get("attempted_owners") or []]
            snap.reference.update({"attempted_owners": owners})
            touched.add(snap.id)
        out["song_requests"] = len(touched)

        # Votes are keyed "{email}__{date}": re-key them so the vote totals stay explainable.
        votes = 0
        for snap in self.db.collection(SONG_REQUEST_VOTES_COLLECTION).where(
                filter=FieldFilter("voter_email", "==", email)).stream():
            day = (snap.to_dict() or {}).get("voted_date") or snap.id.rsplit("__", 1)[-1]
            self._move_doc(SONG_REQUEST_VOTES_COLLECTION, snap, f"{tomb}__{day}", {"voter_email": tomb})
            votes += 1
        out["song_request_votes"] = votes

        # Per-day submission-cap markers hold nothing but the email: just delete them.
        markers = 0
        for snap in self.db.collection(SONG_REQUEST_SUBMISSIONS_COLLECTION).where(
                filter=FieldFilter("email", "==", email)).stream():
            snap.reference.delete()
            markers += 1
        out["song_request_submissions"] = markers
        return out

    # ------------------------------------------------------------ kjbox queue

    def list_kjbox_pending(self, since: Optional[datetime] = None) -> List[dict]:
        query = self.db.collection(ACCOUNT_DELETIONS_COLLECTION).where(
            filter=FieldFilter("targets.kjbox", "==", "pending")
        )
        since = _aware(since) if since is not None else None
        out = []
        for snap in query.stream():
            data = snap.to_dict() or {}
            created = _aware(data.get("created_at"))
            if since is not None and (created is None or created <= since):
                continue
            out.append({"id": snap.id, "email_hash": data.get("email_hash"), "created_at": _iso(created)})
        out.sort(key=lambda e: e["created_at"] or "")
        return out

    def ack_kjbox(self, deletion_doc_id: str) -> bool:
        ref = self.db.collection(ACCOUNT_DELETIONS_COLLECTION).document(deletion_doc_id)
        if not ref.get().exists:
            return False
        ref.update({"targets.kjbox": "done", "kjbox_acked_at": _now()})
        return True


def is_deleted_email(db, email: str) -> bool:
    """True when an account with this email was self-deleted before (no second welcome credit)."""
    query = db.collection(DELETED_USERS_COLLECTION).where(
        filter=FieldFilter("email_sha256", "==", email_sha256(email))
    ).limit(1)
    return any(True for _ in query.stream())


def get_account_deletion_service() -> AccountDeletionService:
    from backend.services.user_service import get_user_service

    return AccountDeletionService(get_user_service().db)

