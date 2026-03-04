"""
Stripe admin service for payment analytics and management.

Handles:
- Storing enriched payment data from webhooks
- Revenue analytics and reporting
- Payment listing and detail views
- Stripe balance and payout queries
- Refund processing
- Dispute monitoring
- Webhook event logging
"""
import logging
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple

import stripe
from google.cloud import firestore

from backend.config import get_settings


logger = logging.getLogger(__name__)

STRIPE_PAYMENTS_COLLECTION = "stripe_payments"
STRIPE_WEBHOOK_EVENTS_COLLECTION = "stripe_webhook_events"


class StripeAdminService:
    """Service for Stripe admin analytics and payment management."""

    def __init__(self):
        self.settings = get_settings()
        self.db = firestore.Client()
        self._revenue_cache: Optional[Dict[str, Any]] = None
        self._revenue_cache_time: float = 0
        self._revenue_cache_ttl: float = 300  # 5 minutes

    # =========================================================================
    # Payment Storage
    # =========================================================================

    def store_payment(self, session: Dict[str, Any]) -> bool:
        """
        Store enriched payment data from a completed Checkout Session.

        Args:
            session: Stripe Checkout Session object (expanded with payment_intent,
                     charge, and balance_transaction)

        Returns:
            True if stored successfully
        """
        try:
            session_id = session.get("id", "")
            metadata = session.get("metadata", {})

            # Extract payment intent and charge details
            payment_intent = session.get("payment_intent", {})
            if isinstance(payment_intent, str):
                # Not expanded - just store the ID
                payment_intent_id = payment_intent
                charge_data = {}
                balance_txn = {}
            else:
                payment_intent_id = payment_intent.get("id", "")
                charges = payment_intent.get("latest_charge", {})
                if isinstance(charges, str):
                    charge_data = {}
                    balance_txn = {}
                else:
                    charge_data = charges or {}
                    bt = charge_data.get("balance_transaction", {})
                    balance_txn = bt if isinstance(bt, dict) else {}

            # Extract payment method details
            payment_method_details = charge_data.get("payment_method_details", {})
            pm_type = payment_method_details.get("type", "unknown")
            card_info = payment_method_details.get("card", {})

            # Determine order type
            order_type = metadata.get("order_type", "credit_purchase")
            package_id = metadata.get("package_id")
            credits = 0
            try:
                credits = int(metadata.get("credits", 0))
            except (ValueError, TypeError):
                pass

            # Build product description
            if order_type == "made_for_you":
                artist = metadata.get("artist", "")
                title = metadata.get("title", "")
                product_description = f"Made For You: {artist} - {title}"
            else:
                from backend.services.stripe_service import CREDIT_PACKAGES
                pkg = CREDIT_PACKAGES.get(package_id, {})
                product_description = pkg.get("name", f"{credits} Credits")

            # Extract discount info
            total_details = session.get("total_details", {})
            discount_amount = 0
            promotion_code = None
            if total_details:
                discount_obj = total_details.get("amount_discount", 0)
                discount_amount = discount_obj if isinstance(discount_obj, int) else 0

            # Determine if test payment
            is_test = not (session.get("livemode", False))

            payment_data = {
                "session_id": session_id,
                "payment_intent_id": payment_intent_id,
                "charge_id": charge_data.get("id", ""),
                # Financial (cents)
                "amount_total": session.get("amount_total", 0),
                "currency": session.get("currency", "usd"),
                "stripe_fee": balance_txn.get("fee", 0),
                "net_amount": balance_txn.get("net", 0),
                # Customer
                "customer_email": (
                    metadata.get("user_email")
                    or metadata.get("customer_email")
                    or session.get("customer_email", "")
                ).lower().strip(),
                "customer_name": session.get("customer_details", {}).get("name", ""),
                "stripe_customer_id": session.get("customer", ""),
                # Payment method
                "payment_method_type": pm_type,
                "card_brand": card_info.get("brand", ""),
                "card_last4": card_info.get("last4", ""),
                # Order details
                "order_type": order_type,
                "package_id": package_id,
                "credits_granted": credits,
                "product_description": product_description,
                # Made-for-you fields
                "artist": metadata.get("artist"),
                "title": metadata.get("title"),
                "job_id": metadata.get("job_id"),
                # Status
                "status": "succeeded",
                "refund_amount": 0,
                "refund_id": None,
                "refunded_at": None,
                "refund_reason": None,
                # Timestamps
                "created_at": datetime.utcfromtimestamp(session["created"]) if session.get("created") else datetime.utcnow(),
                "processed_at": datetime.utcnow(),
                # Metadata
                "is_test": is_test,
                "promotion_code": promotion_code,
                "discount_amount": discount_amount,
            }

            doc_ref = self.db.collection(STRIPE_PAYMENTS_COLLECTION).document(session_id)
            doc_ref.set(payment_data)

            # Invalidate revenue cache
            self._revenue_cache = None

            logger.info(
                f"Stored payment data for session {session_id}: "
                f"${payment_data['amount_total'] / 100:.2f} from {payment_data['customer_email']}"
            )
            return True

        except Exception:
            logger.exception(f"Error storing payment data for session {session.get('id', 'unknown')}")
            return False

    def update_payment_status(self, session_id: str, updates: Dict[str, Any]) -> bool:
        """Update payment status fields (for refunds/disputes)."""
        try:
            doc_ref = self.db.collection(STRIPE_PAYMENTS_COLLECTION).document(session_id)
            doc = doc_ref.get()
            if not doc.exists:
                logger.warning(f"Payment {session_id} not found for status update")
                return False

            doc_ref.update(updates)
            self._revenue_cache = None
            return True
        except Exception:
            logger.exception(f"Error updating payment status for {session_id}")
            return False

    # =========================================================================
    # Revenue Analytics
    # =========================================================================

    def get_revenue_summary(
        self, days: int = 30, exclude_test: bool = True
    ) -> Dict[str, Any]:
        """
        Get revenue summary metrics.

        Returns total gross, fees, net, refunds, count, AOV, revenue by product type.
        Uses in-memory cache with 5-minute TTL.
        """
        cache_key = f"{days}_{exclude_test}"
        now = time.time()
        if (
            self._revenue_cache
            and self._revenue_cache.get("_key") == cache_key
            and (now - self._revenue_cache_time) < self._revenue_cache_ttl
        ):
            return self._revenue_cache

        try:
            collection = self.db.collection(STRIPE_PAYMENTS_COLLECTION)
            query = collection

            if days > 0:
                cutoff = datetime.utcnow() - timedelta(days=days)
                query = query.where("created_at", ">=", cutoff)

            total_gross = 0
            total_fees = 0
            total_net = 0
            total_refunds = 0
            count = 0
            by_type: Dict[str, int] = {}

            for doc in query.stream():
                data = doc.to_dict()
                if exclude_test and data.get("is_test"):
                    continue

                amount = data.get("amount_total", 0)
                total_gross += amount
                total_fees += data.get("stripe_fee", 0)
                total_net += data.get("net_amount", 0)
                total_refunds += data.get("refund_amount", 0)
                count += 1

                order_type = data.get("order_type", "credit_purchase")
                by_type[order_type] = by_type.get(order_type, 0) + amount

            aov = total_gross / count if count > 0 else 0

            result = {
                "_key": cache_key,
                "total_gross": total_gross,
                "total_fees": total_fees,
                "total_net": total_net,
                "total_refunds": total_refunds,
                "transaction_count": count,
                "average_order_value": round(aov),
                "revenue_by_type": by_type,
            }

            self._revenue_cache = result
            self._revenue_cache_time = now
            return result

        except Exception:
            logger.exception("Error calculating revenue summary")
            return {
                "total_gross": 0,
                "total_fees": 0,
                "total_net": 0,
                "total_refunds": 0,
                "transaction_count": 0,
                "average_order_value": 0,
                "revenue_by_type": {},
            }

    def get_revenue_by_period(
        self,
        days: int = 30,
        group_by: str = "day",
        exclude_test: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        Get revenue grouped by time period for charting.

        Args:
            days: Number of days to look back
            group_by: 'day' or 'week' or 'month'
            exclude_test: Exclude test transactions

        Returns:
            List of {date, gross, net, fees, count} dicts
        """
        try:
            collection = self.db.collection(STRIPE_PAYMENTS_COLLECTION)
            cutoff = datetime.utcnow() - timedelta(days=days)
            query = collection.where("created_at", ">=", cutoff)

            buckets: Dict[str, Dict[str, Any]] = {}

            for doc in query.stream():
                data = doc.to_dict()
                if exclude_test and data.get("is_test"):
                    continue

                created = data.get("created_at")
                if not created:
                    continue
                if isinstance(created, str):
                    try:
                        created = datetime.fromisoformat(created.replace("Z", "+00:00"))
                    except Exception:
                        continue

                if group_by == "week":
                    # Start of week (Monday)
                    start = created - timedelta(days=created.weekday())
                    key = start.strftime("%Y-%m-%d")
                elif group_by == "month":
                    key = created.strftime("%Y-%m-01")
                else:
                    key = created.strftime("%Y-%m-%d")

                if key not in buckets:
                    buckets[key] = {"date": key, "gross": 0, "net": 0, "fees": 0, "count": 0}

                buckets[key]["gross"] += data.get("amount_total", 0)
                buckets[key]["net"] += data.get("net_amount", 0)
                buckets[key]["fees"] += data.get("stripe_fee", 0)
                buckets[key]["count"] += 1

            result = sorted(buckets.values(), key=lambda x: x["date"])
            return result

        except Exception:
            logger.exception("Error getting revenue by period")
            return []

    # =========================================================================
    # Payment Listing & Details
    # =========================================================================

    def list_payments(
        self,
        limit: int = 50,
        offset: int = 0,
        order_type: Optional[str] = None,
        status: Optional[str] = None,
        email: Optional[str] = None,
        exclude_test: bool = True,
    ) -> Dict[str, Any]:
        """
        List payments with pagination and filters.

        Returns:
            {payments: [...], total: int, has_more: bool}
        """
        try:
            collection = self.db.collection(STRIPE_PAYMENTS_COLLECTION)
            query = collection.order_by("created_at", direction=firestore.Query.DESCENDING)

            if order_type:
                query = collection.where("order_type", "==", order_type).order_by(
                    "created_at", direction=firestore.Query.DESCENDING
                )
            if status:
                query = collection.where("status", "==", status).order_by(
                    "created_at", direction=firestore.Query.DESCENDING
                )
            if email:
                query = collection.where("customer_email", "==", email.lower()).order_by(
                    "created_at", direction=firestore.Query.DESCENDING
                )

            # Stream and filter (Firestore doesn't support multiple inequality filters)
            payments = []
            skipped = 0
            total = 0

            for doc in query.limit(1000).stream():
                data = doc.to_dict()
                data["session_id"] = doc.id

                if exclude_test and data.get("is_test"):
                    continue

                # Apply filters that couldn't be done in query
                if order_type and data.get("order_type") != order_type:
                    continue
                if status and data.get("status") != status:
                    continue
                if email and data.get("customer_email", "").lower() != email.lower():
                    continue

                total += 1
                if skipped < offset:
                    skipped += 1
                    continue
                if len(payments) < limit:
                    # Convert datetime objects for JSON serialization
                    for key in ["created_at", "processed_at", "refunded_at"]:
                        if isinstance(data.get(key), datetime):
                            data[key] = data[key].isoformat()
                    payments.append(data)

            return {
                "payments": payments,
                "total": total,
                "has_more": total > offset + limit,
            }

        except Exception:
            logger.exception("Error listing payments")
            return {"payments": [], "total": 0, "has_more": False}

    def get_payment_detail(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Get full payment details for a single payment.

        Combines Firestore data with on-demand Stripe API call for receipt_url.
        """
        try:
            doc_ref = self.db.collection(STRIPE_PAYMENTS_COLLECTION).document(session_id)
            doc = doc_ref.get()
            if not doc.exists:
                return None

            data = doc.to_dict()
            data["session_id"] = doc.id

            # Convert datetime objects
            for key in ["created_at", "processed_at", "refunded_at"]:
                if isinstance(data.get(key), datetime):
                    data[key] = data[key].isoformat()

            # On-demand: fetch receipt URL from Stripe
            charge_id = data.get("charge_id")
            if charge_id:
                try:
                    charge = stripe.Charge.retrieve(charge_id)
                    data["receipt_url"] = charge.get("receipt_url")
                    data["stripe_dashboard_url"] = (
                        f"https://dashboard.stripe.com/payments/{data.get('payment_intent_id', '')}"
                    )
                except Exception as e:
                    logger.warning(f"Could not fetch charge {charge_id}: {e}")
                    data["receipt_url"] = None

            return data

        except Exception:
            logger.exception(f"Error getting payment detail for {session_id}")
            return None

    def get_user_payment_history(self, email: str) -> Dict[str, Any]:
        """Get all payments for a user with total_spent."""
        try:
            collection = self.db.collection(STRIPE_PAYMENTS_COLLECTION)
            query = (
                collection.where("customer_email", "==", email.lower())
                .order_by("created_at", direction=firestore.Query.DESCENDING)
            )

            payments = []
            total_spent = 0
            total_refunded = 0

            for doc in query.stream():
                data = doc.to_dict()
                data["session_id"] = doc.id
                total_spent += data.get("amount_total", 0)
                total_refunded += data.get("refund_amount", 0)

                for key in ["created_at", "processed_at", "refunded_at"]:
                    if isinstance(data.get(key), datetime):
                        data[key] = data[key].isoformat()

                payments.append(data)

            first_payment = payments[-1]["created_at"] if payments else None
            last_payment = payments[0]["created_at"] if payments else None

            return {
                "email": email,
                "payments": payments,
                "total_spent": total_spent,
                "total_refunded": total_refunded,
                "net_spent": total_spent - total_refunded,
                "payment_count": len(payments),
                "first_payment_at": first_payment,
                "last_payment_at": last_payment,
            }

        except Exception:
            logger.exception(f"Error getting payment history for {email}")
            return {
                "email": email,
                "payments": [],
                "total_spent": 0,
                "total_refunded": 0,
                "net_spent": 0,
                "payment_count": 0,
                "first_payment_at": None,
                "last_payment_at": None,
            }

    # =========================================================================
    # Stripe Balance & Payouts
    # =========================================================================

    def get_stripe_balance(self) -> Dict[str, Any]:
        """Get current Stripe account balance (available + pending)."""
        try:
            balance = stripe.Balance.retrieve()
            available = sum(b.get("amount", 0) for b in balance.get("available", []))
            pending = sum(b.get("amount", 0) for b in balance.get("pending", []))
            return {
                "available": available,
                "pending": pending,
                "currency": "usd",
            }
        except Exception:
            logger.exception("Error fetching Stripe balance")
            return {"available": 0, "pending": 0, "currency": "usd"}

    def get_recent_payouts(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent payouts from Stripe."""
        try:
            payouts = stripe.Payout.list(limit=limit)
            result = []
            for p in payouts.get("data", []):
                result.append({
                    "id": p.get("id"),
                    "amount": p.get("amount"),
                    "currency": p.get("currency"),
                    "status": p.get("status"),
                    "arrival_date": p.get("arrival_date"),
                    "created": p.get("created"),
                    "description": p.get("description"),
                    "method": p.get("method"),
                })
            return result
        except Exception:
            logger.exception("Error fetching payouts")
            return []

    # =========================================================================
    # Disputes
    # =========================================================================

    def get_recent_disputes(self) -> List[Dict[str, Any]]:
        """Get open and recent disputes from Stripe."""
        try:
            disputes = stripe.Dispute.list(limit=20)
            result = []
            for d in disputes.get("data", []):
                result.append({
                    "id": d.get("id"),
                    "amount": d.get("amount"),
                    "currency": d.get("currency"),
                    "status": d.get("status"),
                    "reason": d.get("reason"),
                    "charge_id": d.get("charge"),
                    "created": d.get("created"),
                    "evidence_due_by": d.get("evidence_details", {}).get("due_by"),
                    "payment_intent_id": d.get("payment_intent"),
                })
            return result
        except Exception:
            logger.exception("Error fetching disputes")
            return []

    # =========================================================================
    # Refund Processing
    # =========================================================================

    def process_refund(
        self,
        session_id: str,
        amount: Optional[int] = None,
        reason: str = "requested_by_customer",
    ) -> Tuple[bool, str]:
        """
        Process a refund for a payment.

        Args:
            session_id: Checkout session ID
            amount: Refund amount in cents (None for full refund)
            reason: Refund reason

        Returns:
            (success, message)
        """
        try:
            # Get payment record
            doc_ref = self.db.collection(STRIPE_PAYMENTS_COLLECTION).document(session_id)
            doc = doc_ref.get()
            if not doc.exists:
                return False, "Payment not found"

            data = doc.to_dict()
            payment_intent_id = data.get("payment_intent_id")
            if not payment_intent_id:
                return False, "No payment intent ID found"

            current_refund = data.get("refund_amount", 0)
            original_amount = data.get("amount_total", 0)
            refund_amount = amount or (original_amount - current_refund)

            if refund_amount <= 0:
                return False, "Invalid refund amount"
            if current_refund + refund_amount > original_amount:
                return False, f"Refund would exceed original amount (${original_amount / 100:.2f})"

            # Create Stripe refund
            refund_params: Dict[str, Any] = {
                "payment_intent": payment_intent_id,
                "amount": refund_amount,
                "reason": reason,
            }
            refund = stripe.Refund.create(**refund_params)

            # Update Firestore record
            new_total_refund = current_refund + refund_amount
            new_status = "refunded" if new_total_refund >= original_amount else "partially_refunded"

            doc_ref.update({
                "status": new_status,
                "refund_amount": new_total_refund,
                "refund_id": refund.get("id"),
                "refunded_at": datetime.utcnow(),
                "refund_reason": reason,
            })

            self._revenue_cache = None

            logger.info(
                f"Processed refund for {session_id}: "
                f"${refund_amount / 100:.2f} ({new_status})"
            )
            return True, f"Refund of ${refund_amount / 100:.2f} processed successfully"

        except stripe.error.StripeError as e:
            logger.error(f"Stripe refund error for {session_id}: {e}")
            return False, f"Stripe error: {str(e)}"
        except Exception:
            logger.exception(f"Error processing refund for {session_id}")
            return False, "Unexpected error processing refund"

    # =========================================================================
    # Webhook Event Logging
    # =========================================================================

    def log_webhook_event(
        self,
        event_id: str,
        event_type: str,
        status: str = "processing",
        session_id: Optional[str] = None,
        customer_email: Optional[str] = None,
        summary: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Log a webhook event for audit trail."""
        try:
            doc_ref = self.db.collection(STRIPE_WEBHOOK_EVENTS_COLLECTION).document(event_id)
            data = {
                "event_id": event_id,
                "event_type": event_type,
                "created_at": datetime.utcnow(),
                "status": status,
                "session_id": session_id,
                "customer_email": customer_email,
                "summary": summary,
                "error_message": error_message,
            }
            if status in ("success", "error"):
                data["processed_at"] = datetime.utcnow()
            doc_ref.set(data, merge=True)
        except Exception:
            logger.exception(f"Error logging webhook event {event_id}")

    def list_webhook_events(
        self,
        limit: int = 50,
        event_type: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List recent webhook events."""
        try:
            collection = self.db.collection(STRIPE_WEBHOOK_EVENTS_COLLECTION)
            query = collection.order_by("created_at", direction=firestore.Query.DESCENDING)

            if event_type:
                query = collection.where("event_type", "==", event_type).order_by(
                    "created_at", direction=firestore.Query.DESCENDING
                )

            events = []
            for doc in query.limit(limit).stream():
                data = doc.to_dict()
                if status and data.get("status") != status:
                    continue
                for key in ["created_at", "processed_at"]:
                    if isinstance(data.get(key), datetime):
                        data[key] = data[key].isoformat()
                events.append(data)

            return events

        except Exception:
            logger.exception("Error listing webhook events")
            return []

    def normalize_customer_emails(self) -> Dict[str, Any]:
        """Backfill: lowercase all customer_email values in stripe_payments."""
        try:
            collection = self.db.collection(STRIPE_PAYMENTS_COLLECTION)
            updated = 0
            skipped = 0

            for doc in collection.stream():
                data = doc.to_dict()
                email = data.get("customer_email", "")
                normalized = email.lower().strip()
                if email != normalized:
                    doc.reference.update({"customer_email": normalized})
                    updated += 1
                else:
                    skipped += 1

            return {
                "status": "success",
                "updated": updated,
                "skipped": skipped,
                "total": updated + skipped,
            }
        except Exception:
            logger.exception("Error normalizing customer emails")
            raise


# Global instance
_stripe_admin_service = None


def get_stripe_admin_service() -> StripeAdminService:
    """Get the global Stripe admin service instance."""
    global _stripe_admin_service
    if _stripe_admin_service is None:
        _stripe_admin_service = StripeAdminService()
    return _stripe_admin_service
