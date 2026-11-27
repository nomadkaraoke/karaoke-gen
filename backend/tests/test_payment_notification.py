"""Payment notification emails: one clear '[No action]' / '[Action needed]' email per payment."""
import logging
from unittest.mock import MagicMock, patch

import pytest

from backend.services import payment_notification as pn

TIP_LINK = "plink_1R08pkJqOK5vtxo4qf1Sqq6v"


def _tip_session(note=None, link=TIP_LINK):
    return {
        "id": "cs_live_tip",
        "amount_total": 500,
        "currency": "usd",
        "metadata": {},
        "customer_email": None,
        "customer_details": {"name": "Pat Singer", "email": "pat@example.com"},
        "payment_link": link,
        "payment_intent": "pi_123",
        "custom_fields": [
            {"key": "anyothernotescomments", "label": {"custom": "Any other notes / comments"},
             "type": "text", "text": {"value": note}},
        ],
    }


def _credit_session():
    return {
        "id": "cs_live_credits",
        "amount_total": 1500,
        "currency": "usd",
        "metadata": {"user_email": "buyer@example.com", "credits": "5", "package_id": "5_credits"},
        "customer_details": {"name": "Bea Buyer", "email": "buyer@example.com"},
        "payment_intent": {"id": "pi_456"},
        "line_items": {"data": [
            {"amount_total": 1000, "price": {"lookup_key": None, "metadata": {}}},
            {"amount_total": 500, "price": {"lookup_key": "gen_checkout_tip_usd_1", "metadata": {}}},
        ]},
    }


class TestBuildPaymentNotice:
    def test_tip_without_note_is_no_action(self):
        n = pn.build_payment_notice(_tip_session(), pn.OUTCOME_NON_GEN)
        assert n.subject == "[No action] Tip: $5.00 from Pat Singer"
        assert n.action_needed is False
        assert "Note left: none" in n.lines

    def test_tip_with_blank_note_is_no_action(self):
        n = pn.build_payment_notice(_tip_session(note="   "), pn.OUTCOME_NON_GEN)
        assert n.action_needed is False

    def test_tip_with_note_needs_action_and_shows_note(self):
        n = pn.build_payment_notice(_tip_session(note="$10 per song for 4 songs"), pn.OUTCOME_NON_GEN)
        assert n.action_needed is True
        assert n.subject.startswith("[Action needed] Tip link payment: $5.00 from Pat Singer")
        assert "Any other notes / comments: $10 per song for 4 songs" in n.lines

    def test_unknown_payment_link_needs_action(self):
        n = pn.build_payment_notice(_tip_session(link="plink_other"), pn.OUTCOME_NON_GEN)
        assert n.action_needed is True
        assert "not a gen order" in n.subject
        assert any("plink_other" in line for line in n.lines)

    def test_credits_added_is_no_action_and_shows_tip(self):
        n = pn.build_payment_notice(_credit_session(), pn.OUTCOME_CREDITS_ADDED)
        assert n.subject == "[No action] Credits bought: $15.00 from Bea Buyer"
        assert "Bought: 5 credits on gen.nomadkaraoke.com" in n.lines
        assert "Includes a tip of $5.00" in n.lines

    def test_credits_failed_needs_action_with_reason(self):
        n = pn.build_payment_notice(_credit_session(), pn.OUTCOME_CREDITS_FAILED, "user not found")
        assert n.action_needed is True
        assert n.subject.startswith("[Action needed] Credits NOT added")
        assert "Problem: user not found" in n.lines

    def test_made_for_you_is_skipped(self):
        assert pn.build_payment_notice({"metadata": {"order_type": "made_for_you"}},
                                       pn.OUTCOME_MADE_FOR_YOU) is None

    def test_no_name_falls_back_to_email(self):
        s = _tip_session()
        s["customer_details"] = {"email": "pat@example.com"}
        assert pn.build_payment_notice(s, pn.OUTCOME_NON_GEN).subject.endswith("from pat@example.com")

    def test_non_usd_currency(self):
        s = _tip_session()
        s["currency"] = "gbp"
        assert "5.00 GBP" in pn.build_payment_notice(s, pn.OUTCOME_NON_GEN).subject


class TestSendPaymentNotification:
    def test_sends_to_admin_with_dashboard_link(self, monkeypatch):
        monkeypatch.delenv("ADMIN_NOTIFICATION_EMAIL", raising=False)
        email = MagicMock()
        email.send_email.return_value = True
        assert pn.send_payment_notification(email, _tip_session(), pn.OUTCOME_NON_GEN) is True
        kw = email.send_email.call_args.kwargs
        assert kw["to_email"] == "admin@nomadkaraoke.com"
        assert kw["subject"].startswith("[No action]")
        assert "https://dashboard.stripe.com/payments/pi_123" in kw["text_content"]
        assert "https://dashboard.stripe.com/payments/pi_123" in kw["html_content"]

    def test_expanded_payment_intent_object(self):
        email = MagicMock()
        pn.send_payment_notification(email, _credit_session(), pn.OUTCOME_CREDITS_ADDED)
        assert "payments/pi_456" in email.send_email.call_args.kwargs["text_content"]

    def test_note_is_html_escaped(self):
        email = MagicMock()
        pn.send_payment_notification(email, _tip_session(note="<b>hi</b>"), pn.OUTCOME_NON_GEN)
        assert "<b>hi</b>" not in email.send_email.call_args.kwargs["html_content"]

    def test_made_for_you_sends_nothing(self):
        email = MagicMock()
        assert pn.send_payment_notification(email, {"metadata": {}}, pn.OUTCOME_MADE_FOR_YOU) is False
        email.send_email.assert_not_called()

    def test_email_failure_never_raises(self, caplog):
        email = MagicMock()
        email.send_email.side_effect = RuntimeError("postmark down")
        with caplog.at_level(logging.WARNING):
            assert pn.send_payment_notification(email, _tip_session(), pn.OUTCOME_NON_GEN) is False
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def _run_webhook(session, stripe_service, user_service, email_service):
    from backend.api.routes import users as users_routes

    event = {"id": "evt_1", "type": "checkout.session.completed", "data": {"object": dict(session)}}
    stripe_service.verify_webhook_signature.return_value = (True, event, "ok")
    request = MagicMock()

    async def body():
        return b"{}"
    request.body = body
    with patch("backend.services.stripe_admin_service.get_stripe_admin_service", return_value=MagicMock()), \
         patch("stripe.checkout.Session.retrieve", return_value=dict(session)):
        return await users_routes.stripe_webhook(
            request=request, stripe_signature="sig", stripe_service=stripe_service,
            user_service=user_service, email_service=email_service,
        )


def _user_service():
    us = MagicMock()
    us.is_stripe_session_processed.return_value = False
    us.get_user_doc_ref.return_value = None
    return us


@pytest.mark.asyncio
async def test_webhook_emails_no_action_for_tip():
    email = MagicMock()
    stripe_service = MagicMock()
    await _run_webhook(_tip_session(), stripe_service, _user_service(), email)
    subjects = [c.kwargs["subject"] for c in email.send_email.call_args_list]
    assert subjects == ["[No action] Tip: $5.00 from Pat Singer"]


@pytest.mark.asyncio
async def test_webhook_emails_no_action_for_credit_purchase():
    email = MagicMock()
    stripe_service = MagicMock()
    stripe_service.handle_checkout_completed.return_value = (True, "buyer@example.com", 5, "ok")
    stripe_service.referral_base_cents.return_value = 0
    us = _user_service()
    us.add_credits.return_value = (True, 5, "ok")
    with patch("backend.services.referral_service.get_referral_service", return_value=MagicMock()):
        await _run_webhook(_credit_session(), stripe_service, us, email)
    subjects = [c.kwargs["subject"] for c in email.send_email.call_args_list]
    assert subjects == ["[No action] Credits bought: $15.00 from Bea Buyer"]


@pytest.mark.asyncio
async def test_webhook_emails_action_needed_when_credits_not_added():
    email = MagicMock()
    stripe_service = MagicMock()
    stripe_service.handle_checkout_completed.return_value = (True, "buyer@example.com", 5, "ok")
    us = _user_service()
    us.add_credits.return_value = (False, 0, "user not found")
    await _run_webhook(_credit_session(), stripe_service, us, email)
    call = email.send_email.call_args
    assert call.kwargs["subject"].startswith("[Action needed] Credits NOT added")
    assert "Problem: user not found" in call.kwargs["text_content"]


@pytest.mark.asyncio
async def test_webhook_redelivery_sends_no_second_email():
    email = MagicMock()
    us = _user_service()
    us.is_stripe_session_processed.return_value = True
    await _run_webhook(_tip_session(), MagicMock(), us, email)
    email.send_email.assert_not_called()


def test_rejected_send_logs_warning(caplog):
    email = MagicMock()
    email.send_email.return_value = False
    with caplog.at_level(logging.WARNING):
        assert pn.send_payment_notification(email, _tip_session(), pn.OUTCOME_NON_GEN) is False
    assert any("not sent" in r.message and r.levelno == logging.WARNING for r in caplog.records)
