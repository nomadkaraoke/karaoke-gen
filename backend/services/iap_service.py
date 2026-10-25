"""
In-app purchases (App Store / Play Store credit packs) via RevenueCat webhooks.

RevenueCat posts one event per store transaction. A consumable credit pack
arrives as ``NON_RENEWING_PURCHASE``; a refund arrives as ``CANCELLATION``
(``cancel_reason`` CUSTOMER_SUPPORT, negative price). ``REFUND`` is accepted as
an alias (local stack fake-iap routes).

Exactly-once: each grant / debit runs in ONE Firestore transaction that reads
and ``create``s a marker in ``processed_revenuecat_transactions``
(``{store}:{transaction_id}`` for a purchase, ``…:refund`` for its refund),
updates ``gen_users.credits`` and appends to ``credit_transactions``. A replay
or a concurrent duplicate sees the marker and changes nothing. The
``store_payments`` record (admin revenue) and the referral earning are written
after the commit: the payment doc is keyed by transaction so a rewrite is
harmless, and the earning is recorded only by the delivery that granted.

A refund debits the credits the purchase granted, from the account that
received them, even when that takes the balance below zero (credits already
spent). Job creation refuses a negative balance with 402.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from typing import Optional

from google.api_core import exceptions as google_exceptions

from backend.models.user import CreditTransaction
from backend.services.login_code_store import TransactionContention, run_firestore_transaction
from backend.services.stripe_service import CREDIT_PACKAGES
from backend.services.user_service import MAX_CREDIT_TRANSACTIONS

logger = logging.getLogger(__name__)

PROCESSED_TRANSACTIONS_COLLECTION = "processed_revenuecat_transactions"
STORE_PAYMENTS_COLLECTION = "store_payments"

PURCHASE_REASON = "iap_purchase"
REFUND_REASON = "iap_refund"

PURCHASE_EVENT_TYPES = frozenset({"NON_RENEWING_PURCHASE"})
REFUND_EVENT_TYPES = frozenset({"CANCELLATION", "REFUND"})

# RevenueCat ``store`` → our payment source (also the referral earning ``source``).
STORE_SOURCES = {
    "APP_STORE": "app_store",
    "MAC_APP_STORE": "app_store",
    "PLAY_STORE": "play_store",
}

# Used when an event lacks ``takehome_percentage`` (store commission ~15%).
DEFAULT_TAKEHOME = Decimal("0.85")

ANONYMOUS_ID_PREFIX = "$RCAnonymousID:"


@dataclass
class IapResult:
    action: str  # granted | debited | duplicate | ignored | unknown_user | unknown_product
    credits: Optional[int] = None  # balance after a grant / debit
    email: Optional[str] = None


def package_for_product(product_id: Optional[str]) -> Optional[tuple]:
    """(package_id, package) for a store product id, or None."""
    for pkg_id, pkg in CREDIT_PACKAGES.items():
        if product_id and pkg.get("store_product_id") == product_id:
            return pkg_id, pkg
    return None


def _decimal(value) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def price_cents(event: dict) -> int:
    """Gross USD cents (RevenueCat ``price`` is USD), rounded down, never negative."""
    price = _decimal(event.get("price")) or Decimal(0)
    return int((abs(price) * 100).to_integral_value(rounding=ROUND_FLOOR))


def net_cents(event: dict) -> int:
    """USD cents after the store's cut: price × takehome_percentage (default 0.85), rounded down."""
    price = _decimal(event.get("price")) or Decimal(0)
    takehome = _decimal(event.get("takehome_percentage"))
    if takehome is None:
        takehome = DEFAULT_TAKEHOME
    return int((abs(price) * 100 * takehome).to_integral_value(rounding=ROUND_FLOOR))


class IapService:
    def __init__(self, db, user_service, referral_service=None, transaction_runner=None):
        self.db = db
        self.user_service = user_service
        self._referral_service = referral_service
        # Injectable for unit tests (in-memory Firestore fake).
        self._transaction_runner = transaction_runner

    def _run_transaction(self, fn):
        if self._transaction_runner is not None:
            return self._transaction_runner(fn)
        try:
            return run_firestore_transaction(self.db, fn)
        except google_exceptions.Aborted as exc:
            # A transactional read hit a lock timeout under contention (the
            # library retries only commit-time aborts). Nothing was written.
            raise TransactionContention() from exc

    def _marker_ref(self, key: str):
        return self.db.collection(PROCESSED_TRANSACTIONS_COLLECTION).document(key)

    def _payment_ref(self, key: str):
        return self.db.collection(STORE_PAYMENTS_COLLECTION).document(key)

    # ------------------------------------------------------------------ entry

    def handle_event(self, event: dict) -> IapResult:
        """Apply one RevenueCat event (the ``event`` object of the webhook body)."""
        event_type = event.get("type")
        source = STORE_SOURCES.get(event.get("store") or "")
        transaction_id = event.get("transaction_id")
        if event_type not in PURCHASE_EVENT_TYPES | REFUND_EVENT_TYPES:
            return IapResult("ignored")
        if not source or not transaction_id:
            logger.info(
                f"RevenueCat {event_type} ignored: store={event.get('store')!r} "
                f"transaction={transaction_id!r}"
            )
            return IapResult("ignored")

        found = package_for_product(event.get("product_id"))
        if not found:
            logger.warning(
                f"RevenueCat {event_type} for unknown product {event.get('product_id')!r} "
                f"({source} transaction {transaction_id}): no credits changed"
            )
            return IapResult("unknown_product")

        key = f"{event.get('store')}:{transaction_id}"
        if event_type in PURCHASE_EVENT_TYPES:
            return self._grant(event, key, source, *found)
        return self._refund(event, key, source)

    # ------------------------------------------------------------------ grant

    def _find_user(self, event: dict):
        candidates = [event.get("app_user_id"), event.get("original_app_user_id")]
        candidates += list(event.get("aliases") or [])
        seen = set()
        for app_user_id in candidates:
            if not app_user_id or app_user_id in seen or app_user_id.startswith(ANONYMOUS_ID_PREFIX):
                continue
            seen.add(app_user_id)
            user = self.user_service.find_by_app_user_id(app_user_id)
            if user:
                return user
        return None

    def _grant(self, event: dict, key: str, source: str, package_id: str, package: dict) -> IapResult:
        user = self._find_user(event)
        if not user:
            logger.warning(
                f"RevenueCat purchase {key} ({event.get('product_id')}) for unknown "
                f"app_user_id {event.get('app_user_id')!r}: recorded as unmatched, no credits granted"
            )
            self._write_payment(key, event, source, package_id, package, email=None, unmatched=True)
            return IapResult("unknown_user")

        email = user.email.lower()
        user_ref = self.user_service.get_user_doc_ref(email)
        if user_ref is None:
            logger.warning(f"RevenueCat purchase {key}: user doc for {email} vanished; recorded as unmatched")
            self._write_payment(key, event, source, package_id, package, email=None, unmatched=True)
            return IapResult("unknown_user")

        credits = int(package["credits"])
        amount_cents = price_cents(event)
        marker_ref = self._marker_ref(key)

        def _txn(transaction):
            if marker_ref.get(transaction=transaction).exists:
                return IapResult("duplicate", email=email)
            snap = user_ref.get(transaction=transaction)
            data = snap.to_dict() if snap.exists else None
            if data is None:
                raise LookupError(f"user {email} not found")
            balance = int(data.get("credits") or 0) + credits
            entry = CreditTransaction(
                id=str(uuid.uuid4()), amount=credits, reason=PURCHASE_REASON,
            ).model_dump(mode="json")
            history = list(data.get("credit_transactions") or [])[-(MAX_CREDIT_TRANSACTIONS - 1):] + [entry]
            transaction.create(marker_ref, {
                "kind": "purchase",
                "store": event.get("store"),
                "transaction_id": event.get("transaction_id"),
                "product_id": event.get("product_id"),
                "package_id": package_id,
                "credits": credits,
                "email": email,
                "app_user_id": event.get("app_user_id"),
                "created_at": datetime.utcnow(),
            })
            transaction.update(user_ref, {
                "credits": balance,
                "credit_transactions": history,
                "total_spent": int(data.get("total_spent") or 0) + amount_cents,
                "updated_at": datetime.utcnow(),
            })
            return IapResult("granted", credits=balance, email=email)

        result = self._run_transaction_once(_txn, email)
        if result.action != "granted":
            logger.info(f"RevenueCat purchase {key} already processed (duplicate delivery)")
            return result

        logger.info(f"IAP: granted {credits} credits to {email} ({key}); balance {result.credits}")
        self._write_payment(key, event, source, package_id, package, email=email, unmatched=False)
        self._record_referral_earning(event, key, source, email)
        return result

    # ----------------------------------------------------------------- refund

    def _refund(self, event: dict, key: str, source: str) -> IapResult:
        purchase_ref = self._marker_ref(key)
        refund_ref = self._marker_ref(f"{key}:refund")

        def _txn(transaction):
            if refund_ref.get(transaction=transaction).exists:
                return IapResult("duplicate")
            purchase_snap = purchase_ref.get(transaction=transaction)
            if not purchase_snap.exists:
                return IapResult("ignored")
            purchase = purchase_snap.to_dict()
            email = purchase["email"]
            credits = int(purchase["credits"])
            user_ref = self.user_service.get_user_doc_ref(email)
            snap = user_ref.get(transaction=transaction) if user_ref is not None else None
            data = snap.to_dict() if snap is not None and snap.exists else None
            if data is None:
                # Account deleted since the purchase: nothing to debit (and an
                # error here would make RevenueCat retry forever).
                return IapResult("ignored", email=email)
            balance = int(data.get("credits") or 0) - credits  # may go negative
            entry = CreditTransaction(
                id=str(uuid.uuid4()), amount=-credits, reason=REFUND_REASON,
            ).model_dump(mode="json")
            history = list(data.get("credit_transactions") or [])[-(MAX_CREDIT_TRANSACTIONS - 1):] + [entry]
            transaction.create(refund_ref, {
                "kind": "refund",
                "store": event.get("store"),
                "transaction_id": event.get("transaction_id"),
                "product_id": event.get("product_id"),
                "credits": credits,
                "email": email,
                "created_at": datetime.utcnow(),
            })
            transaction.update(user_ref, {
                "credits": balance,
                "credit_transactions": history,
                "updated_at": datetime.utcnow(),
            })
            return IapResult("debited", credits=balance, email=email)

        result = self._run_transaction_once(_txn, None)
        if result.action == "ignored":
            logger.warning(
                f"RevenueCat refund {key}: no granted purchase or account on record "
                f"({result.email or 'no purchase'}): nothing debited"
            )
        elif result.action == "duplicate":
            logger.info(f"RevenueCat refund {key} already processed (duplicate delivery)")
        else:
            logger.info(f"IAP: refund {key} debited {result.email}; balance {result.credits}")
        if result.action in ("debited", "ignored"):
            self._mark_payment_refunded(key, event)
        return result

    # ---------------------------------------------------------------- helpers

    def _run_transaction_once(self, fn, email: Optional[str]) -> IapResult:
        try:
            return self._run_transaction(fn)
        except google_exceptions.AlreadyExists:
            # A concurrent delivery created the marker between our read and commit.
            return IapResult("duplicate", email=email)

    def _write_payment(self, key, event, source, package_id, package, *, email, unmatched) -> None:
        gross = price_cents(event)
        net = net_cents(event)
        try:
            self._payment_ref(key).set({
                "payment_id": key,
                "source": source,
                "store": event.get("store"),
                "transaction_id": event.get("transaction_id"),
                "product_id": event.get("product_id"),
                "package_id": package_id,
                "credits_granted": 0 if unmatched else int(package["credits"]),
                "product_description": package.get("name"),
                "order_type": f"{source}_credit_purchase",
                "customer_email": email or "",
                "app_user_id": event.get("app_user_id"),
                "unmatched": unmatched,
                "amount_total": gross,
                "currency": "usd",
                "purchased_currency": event.get("currency"),
                "price_in_purchased_currency": event.get("price_in_purchased_currency"),
                "country_code": event.get("country_code"),
                "takehome_percentage": event.get("takehome_percentage"),
                "store_fee": gross - net,
                "net_amount": net,
                "status": "succeeded",
                "refund_amount": 0,
                "refunded_at": None,
                "is_test": (event.get("environment") or "PRODUCTION").upper() == "SANDBOX",
                "created_at": _event_time(event),
                "processed_at": datetime.utcnow(),
            })
        except Exception:
            logger.warning(f"Failed to store store_payments/{key}", exc_info=True)

    def _mark_payment_refunded(self, key, event) -> None:
        try:
            ref = self._payment_ref(key)
            snap = ref.get()
            refund = (snap.to_dict() or {}).get("amount_total") if snap.exists else None
            ref.set({
                "status": "refunded",
                "refund_amount": refund if refund is not None else price_cents(event),
                "refunded_at": datetime.utcnow(),
            }, merge=True)
        except Exception:
            logger.warning(f"Failed to mark store_payments/{key} refunded", exc_info=True)

    def _record_referral_earning(self, event, key, source, email) -> None:
        if (event.get("environment") or "PRODUCTION").upper() == "SANDBOX":
            return
        amount = net_cents(event)
        if amount <= 0:
            return
        try:
            referral = self._referral_service
            if referral is None:
                from backend.services.referral_service import get_referral_service
                referral = get_referral_service()
            earning = referral.record_earning(
                referred_email=email,
                source_key=key,
                purchase_amount_cents=amount,
                source=source,
            )
            if earning:
                logger.info(
                    f"Referral earning ${earning['earning_amount_cents'] / 100:.2f} "
                    f"for {earning['referrer_email']} ({key})"
                )
        except Exception:
            logger.warning(f"Referral earning recording failed for {key}", exc_info=True)


def _event_time(event: dict) -> datetime:
    ms = event.get("purchased_at_ms") or event.get("event_timestamp_ms")
    try:
        return datetime.utcfromtimestamp(int(ms) / 1000) if ms else datetime.utcnow()
    except (TypeError, ValueError, OverflowError):
        return datetime.utcnow()


def get_iap_service() -> IapService:
    from backend.services.user_service import get_user_service

    user_service = get_user_service()
    return IapService(user_service.db, user_service)
