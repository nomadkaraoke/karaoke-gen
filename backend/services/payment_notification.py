"""One clear email to Andrew per Stripe payment, saying what it was and whether he must act.

Stripe's own "Payment of $X from Y" email can't tell a tip from an order, so a $5 tip
left Andrew wondering whether he owed someone a video (2026-11-27). Gen's webhook knows
what each checkout was, so it sends this instead. Every subject starts with
``[No action]`` or ``[Action needed]``.

Made For You orders are skipped: they already get their own admin email with the job link.
"""
from __future__ import annotations

import html
import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# The custom-amount tip Payment Link behind nomadkaraoke.com/tip, pay.nomadkaraoke.com
# and the kjbox singer page (buy.stripe.com/00geUZgfHdhx6TS001).
TIP_PAYMENT_LINK_IDS = frozenset({"plink_1R08pkJqOK5vtxo4qf1Sqq6v"})

# Webhook outcomes the caller passes in.
OUTCOME_NON_GEN = "non_gen"  # Payment Link or other non-gen checkout: recorded only
OUTCOME_CREDITS_ADDED = "credits_added"
OUTCOME_CREDITS_FAILED = "credits_failed"
OUTCOME_MADE_FOR_YOU = "made_for_you"

NO_ACTION = "[No action]"
ACTION_NEEDED = "[Action needed]"


@dataclass
class PaymentNotice:
    subject: str
    action_needed: bool
    lines: List[str]  # plain-text body lines (label: value)
    next_step: str


def _money(cents: Optional[int], currency: Optional[str]) -> str:
    amount = (cents or 0) / 100
    cur = (currency or "usd").upper()
    return f"${amount:.2f}" if cur == "USD" else f"{amount:.2f} {cur}"


def _customer_notes(session: Dict) -> List[str]:
    """Text the payer typed into the checkout's custom fields (e.g. "Any other notes")."""
    notes = []
    for field in session.get("custom_fields") or []:
        value = ((field or {}).get("text") or {}).get("value")
        if value and str(value).strip():
            label = ((field.get("label") or {}).get("custom")) or field.get("key") or "Note"
            notes.append(f"{label}: {str(value).strip()}")
    return notes


def _tip_cents(session: Dict) -> int:
    """Total of the optional gen-checkout tip line, if the expanded session carries line items."""
    total = 0
    for item in ((session.get("line_items") or {}).get("data")) or []:
        price = item.get("price") or {}
        if price.get("lookup_key") == "gen_checkout_tip_usd_1" or (
            (price.get("metadata") or {}).get("purpose") == "checkout_tip"
        ):
            total += item.get("amount_total") or 0
    return total


def build_payment_notice(
    session: Dict, outcome: str, detail: Optional[str] = None
) -> Optional[PaymentNotice]:
    """Classify a completed checkout. Returns None when no email should be sent."""
    if outcome == OUTCOME_MADE_FOR_YOU:
        return None

    metadata = session.get("metadata") or {}
    details = session.get("customer_details") or {}
    name = details.get("name") or ""
    email = (
        metadata.get("user_email")
        or metadata.get("customer_email")
        or session.get("customer_email")
        or details.get("email")
        or ""
    )
    who = name or email or "an unknown payer"
    amount = _money(session.get("amount_total"), session.get("currency"))
    payment_link = session.get("payment_link")
    notes = _customer_notes(session)

    lines = [f"Amount: {amount}", f"From: {name or '(no name)'} <{email or 'no email'}>"]

    if outcome == OUTCOME_NON_GEN and payment_link in TIP_PAYMENT_LINK_IDS:
        lines.append("Paid via: the tip link (nomadkaraoke.com/tip, kjbox singer page)")
        if notes:
            lines.extend(notes)
            return PaymentNotice(
                subject=f"{ACTION_NEEDED} Tip link payment: {amount} from {who}, with a note",
                action_needed=True,
                lines=lines,
                next_step=(
                    "They left a note. Read it: tip-link payments are sometimes paying "
                    "for songs or a custom order, which gen does not fulfil automatically."
                ),
            )
        lines.append("Note left: none")
        return PaymentNotice(
            subject=f"{NO_ACTION} Tip: {amount} from {who}",
            action_needed=False,
            lines=lines,
            next_step="Nothing to do. It's a tip; it has been recorded with gen's payments.",
        )

    if outcome == OUTCOME_NON_GEN:
        lines.append(f"Paid via: {'Payment Link ' + payment_link if payment_link else 'a non-gen checkout'}")
        lines.extend(notes or ["Note left: none"])
        return PaymentNotice(
            subject=f"{ACTION_NEEDED} Payment: {amount} from {who}, not a gen order",
            action_needed=True,
            lines=lines,
            next_step=(
                "This didn't come from gen's checkout or the tip link, so gen doesn't know "
                "what it's for. Check what they paid for; nothing was fulfilled automatically."
            ),
        )

    credits = metadata.get("credits") or "?"
    if outcome == OUTCOME_CREDITS_ADDED:
        lines.append(f"Bought: {credits} credits on gen.nomadkaraoke.com")
        tip = _tip_cents(session)
        if tip:
            lines.append(f"Includes a tip of {_money(tip, session.get('currency'))}")
        return PaymentNotice(
            subject=f"{NO_ACTION} Credits bought: {amount} from {who}",
            action_needed=False,
            lines=lines,
            next_step="Nothing to do. The credits were added and the customer was emailed.",
        )

    # OUTCOME_CREDITS_FAILED (or anything unexpected)
    lines.append(f"Tried to buy: {credits} credits on gen.nomadkaraoke.com")
    if detail:
        lines.append(f"Problem: {detail}")
    return PaymentNotice(
        subject=f"{ACTION_NEEDED} Credits NOT added: {amount} from {who}",
        action_needed=True,
        lines=lines,
        next_step=(
            "They paid but gen didn't add their credits. Add them by hand in the admin "
            "users page, or refund the payment in Stripe."
        ),
    )


def _dashboard_url(session: Dict) -> str:
    pi = session.get("payment_intent")
    if isinstance(pi, dict):
        pi = pi.get("id")
    if pi:
        return f"https://dashboard.stripe.com/payments/{pi}"
    return "https://dashboard.stripe.com/payments"


def send_payment_notification(
    email_service, session: Dict, outcome: str, detail: Optional[str] = None
) -> bool:
    """Email Andrew about this payment. Never raises: a notification must not break the webhook."""
    try:
        notice = build_payment_notice(session, outcome, detail)
        if notice is None:
            return False
        to_email = os.getenv("ADMIN_NOTIFICATION_EMAIL", "admin@nomadkaraoke.com")
        url = _dashboard_url(session)
        text = "\n".join(
            [notice.next_step, "", *notice.lines, "", f"Stripe: {url}", f"Checkout session: {session.get('id', '')}"]
        )
        colour = "#b45309" if notice.action_needed else "#15803d"
        rows = "".join(f"<li>{html.escape(line)}</li>" for line in notice.lines)
        html_content = (
            f'<p style="font-size:16px;color:{colour};"><strong>{html.escape(notice.next_step)}</strong></p>'
            f"<ul>{rows}</ul>"
            f'<p><a href="{html.escape(url)}">View in Stripe</a> &middot; '
            f"session {html.escape(str(session.get('id', '')))}</p>"
        )
        return bool(
            email_service.send_email(
                to_email=to_email,
                subject=notice.subject,
                html_content=html_content,
                text_content=text,
            )
        )
    except Exception as e:  # best-effort: never let a notification break payment handling
        logger.warning(f"Payment notification failed for {session.get('id')}: {e}")
        return False
