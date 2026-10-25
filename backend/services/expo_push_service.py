"""Expo push notifications for the Nomad Karaoke mobile app.

Tokens live on the gen user (``expo_push_tokens``, one entry per device). A flat
``expo_push_token_values`` array mirrors the token strings so a token can be found
across users with ``array_contains`` (Firestore cannot query inside array objects).

A token belongs to exactly one user: registering it removes it from everyone else,
so a device that signs into another account never receives the previous account's
notifications.

In fake-workers mode (the local E2E stack) ``send`` records to an in-memory outbox
instead of calling Expo.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Literal, Optional

import httpx
from google.cloud.firestore_v1.base_query import FieldFilter

from backend.models.user import ExpoPushToken, effective_push_prefs
from backend.services.fake_workers import fake_workers_enabled
from backend.services.login_code_store import run_firestore_transaction
from backend.services.user_service import USERS_COLLECTION, UserService, get_user_service

logger = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
EXPO_BATCH_SIZE = 100
TOKENS_FIELD = "expo_push_tokens"
VALUES_FIELD = "expo_push_token_values"

PushKind = Literal["ready", "review", "problems"]


def _token_of(entry) -> Optional[str]:
    return entry.get("token") if isinstance(entry, dict) else None


class ExpoPushService:
    def __init__(self, user_service: Optional[UserService] = None, transaction_runner=None, http_post=None):
        self._user_service = user_service
        self._transaction_runner = transaction_runner  # injectable for in-memory Firestore fakes
        self._http_post = http_post or httpx.post

    @property
    def user_service(self) -> UserService:
        return self._user_service or get_user_service()

    @property
    def db(self):
        return self.user_service.db

    def _run_transaction(self, fn):
        if self._transaction_runner is not None:
            return self._transaction_runner(fn)
        return run_firestore_transaction(self.db, fn)

    # ------------------------------------------------------------------ tokens

    def register_token(self, email: str, token: str, platform: str, device_id: str) -> None:
        """Store ``token`` for ``email`` (one entry per device) and remove it from every other user."""
        self.user_service.get_or_create_user(email)
        me_ref = self.user_service.get_user_doc_ref(email)
        if me_ref is None:
            raise ValueError(f"user not found: {email}")
        users = self.db.collection(USERS_COLLECTION)
        now = datetime.now(timezone.utc)

        def txn(transaction):
            # All reads before any write.
            mine = me_ref.get(transaction=transaction)
            others = list(
                users.where(filter=FieldFilter(VALUES_FIELD, "array_contains", token)).stream(transaction=transaction)
            )
            existing = (mine.to_dict() or {}).get(TOKENS_FIELD) or []
            kept = [e for e in existing if _token_of(e) != token and e.get("device_id") != device_id]
            entry = ExpoPushToken(token=token, platform=platform, device_id=device_id, created_at=now).model_dump()
            new_list = kept + [entry]
            for other in others:
                if other.reference.id == me_ref.id:
                    continue
                remaining = [e for e in (other.to_dict() or {}).get(TOKENS_FIELD) or [] if _token_of(e) != token]
                transaction.update(other.reference, {
                    TOKENS_FIELD: remaining,
                    VALUES_FIELD: [_token_of(e) for e in remaining],
                })
            transaction.set(me_ref, {
                TOKENS_FIELD: new_list,
                VALUES_FIELD: [_token_of(e) for e in new_list],
            }, merge=True)

        self._run_transaction(txn)

    def remove_tokens(self, email: str, tokens: List[str]) -> None:
        """Remove ``tokens`` from ``email`` (no-op for unknown users or tokens)."""
        if not tokens:
            return
        me_ref = self.user_service.get_user_doc_ref(email)
        if me_ref is None:
            return
        drop = set(tokens)

        def txn(transaction):
            snap = me_ref.get(transaction=transaction)
            if not snap.exists:
                return
            existing = (snap.to_dict() or {}).get(TOKENS_FIELD) or []
            remaining = [e for e in existing if _token_of(e) not in drop]
            if len(remaining) == len(existing):
                return
            transaction.update(me_ref, {
                TOKENS_FIELD: remaining,
                VALUES_FIELD: [_token_of(e) for e in remaining],
            })

        self._run_transaction(txn)

    # ------------------------------------------------------------------ sending

    def send(self, email: str, kind: PushKind, title: str, body: str, url: str) -> int:
        """Push to every Expo token of ``email`` if their ``kind`` preference is on.

        Returns the number of notifications Expo accepted (or captured, in fake mode).
        Never raises: a failed push must not affect job processing.
        """
        try:
            user = self.user_service.get_user(email)
            if user is None:
                return 0
            if not effective_push_prefs(getattr(user, "push_prefs", None)).get(kind, True):
                logger.debug("Expo push %s suppressed by preference for %s", kind, email)
                return 0
            tokens = [t.token for t in user.expo_push_tokens]
            if not tokens:
                return 0

            if fake_workers_enabled():
                from backend.services.fake_workers.outbox import record_push

                record_push(email, kind, title, body, url)
                return len(tokens)

            return self._send_batches(email, tokens, kind, title, body, url)
        except Exception:
            logger.exception("Expo push failed for %s", email)
            return 0

    def _send_batches(self, email: str, tokens: List[str], kind: str, title: str, body: str, url: str) -> int:
        sent = 0
        dead: List[str] = []
        for i in range(0, len(tokens), EXPO_BATCH_SIZE):
            batch = tokens[i:i + EXPO_BATCH_SIZE]
            messages = [
                {"to": tok, "title": title, "body": body, "sound": "default", "data": {"url": url, "kind": kind}}
                for tok in batch
            ]
            try:
                resp = self._http_post(
                    EXPO_PUSH_URL, json=messages, timeout=15,
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                )
                resp.raise_for_status()
                tickets = resp.json().get("data") or []
            except Exception as exc:
                logger.warning("Expo push batch of %d failed: %s", len(batch), exc)
                continue
            for tok, ticket in zip(batch, tickets):
                if ticket.get("status") == "ok":
                    sent += 1
                elif (ticket.get("details") or {}).get("error") == "DeviceNotRegistered":
                    dead.append(tok)
                else:
                    logger.warning("Expo ticket error for a token of %s: %s", email, ticket.get("message"))
        if dead:
            self.remove_tokens(email, dead)
            logger.info("Removed %d unregistered Expo tokens for %s", len(dead), email)
        return sent


_expo_push_service: Optional[ExpoPushService] = None


def get_expo_push_service() -> ExpoPushService:
    global _expo_push_service
    if _expo_push_service is None:
        _expo_push_service = ExpoPushService()
    return _expo_push_service
