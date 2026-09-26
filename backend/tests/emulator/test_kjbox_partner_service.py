"""
Emulator integration tests for the kjbox partner service.

Runs KjboxPartnerService against a REAL Firestore emulator so the primitives
the security properties rest on are verified for real, not faked: the atomic
Increment attempt counter, create()-only single-use / idempotency markers,
set(merge=True) preserving the precomputed eval, and the FieldFilter queries
behind the signup / show-credit caps. Run with: scripts/run-emulator-tests.sh
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from google.cloud import firestore

from backend.services import kjbox_partner_service as kps

from .conftest import emulators_running

pytestmark = pytest.mark.skipif(
    not emulators_running(),
    reason="GCP emulators not running. Start with: scripts/start-emulators.sh",
)


@pytest.fixture
def svc():
    return kps.KjboxPartnerService(firestore.Client(), pepper="emulator-pepper")


def _email():
    return f"kj-{uuid.uuid4().hex[:8]}@example.com"


def _issue(svc, email, **over):
    kwargs = dict(needs_credit_eval=True, locale="en", ui_locale="en", venue="Dive",
                  ip_address=None, user_agent=None)
    kwargs.update(over)
    return svc.issue_code(email, **kwargs)


def _wrong(code):
    return f"{(int(code) + 1) % 1_000_000:06d}"


class TestCodes:
    def test_round_trip_and_single_use(self, svc):
        email = _email()
        issued = _issue(svc, email)
        assert svc.verify_code(email, _wrong(issued.code))[0] == kps.VerifyOutcome.INVALID
        outcome, record = svc.verify_code(email, issued.code)
        assert outcome == kps.VerifyOutcome.OK and record["email"] == email
        assert svc.verify_code(email, issued.code)[0] == kps.VerifyOutcome.EXPIRED

    def test_attempt_cap_uses_atomic_increment(self, svc):
        email = _email()
        issued = _issue(svc, email)
        for _ in range(kps.MAX_VERIFY_ATTEMPTS):
            assert svc.verify_code(email, _wrong(issued.code))[0] == kps.VerifyOutcome.INVALID
        assert svc.verify_code(email, issued.code)[0] == kps.VerifyOutcome.TOO_MANY_ATTEMPTS
        assert svc.get_code_record(email)["code_hash"] is None

    def test_resend_keeps_eval_and_throttle_history(self, svc):
        email = _email()
        first = _issue(svc, email)
        ref = svc.db.collection(kps.LOGIN_CODES_COLLECTION).document(first.doc_id)
        ref.update({"credit_eval_decision": "grant"})
        second = _issue(svc, email)
        record = svc.get_code_record(email)
        assert record["credit_eval_decision"] == "grant"
        assert second.needs_credit_eval is False  # eval already stored
        assert len(record["send_times"]) == 2
        assert svc.is_email_throttled(email, 2) and not svc.is_email_throttled(email, 3)
        assert svc.verify_code(email, second.code)[0] == kps.VerifyOutcome.OK

    def test_expired(self, svc):
        email = _email()
        issued = _issue(svc, email)
        svc.db.collection(kps.LOGIN_CODES_COLLECTION).document(issued.doc_id).update(
            {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
        )
        assert svc.verify_code(email, issued.code)[0] == kps.VerifyOutcome.EXPIRED


class TestCaps:
    def test_signup_count_window(self, svc):
        before = svc.count_recent_signups()
        svc.record_signup(_email(), "Dive")
        svc.db.collection(kps.SIGNUPS_COLLECTION).document().set(
            {"email": _email(), "created_at": datetime.now(timezone.utc) - timedelta(hours=25)}
        )
        assert svc.count_recent_signups() == before + 1

    def test_show_credit_idempotency_and_count(self, svc):
        email = _email()
        key = f"job-{uuid.uuid4().hex}"
        assert svc.claim_show_credit(key, email, "Dive") is True
        assert svc.claim_show_credit(key, email, "Dive") is False
        assert svc.show_credit_already_processed(key)
        assert svc.count_recent_show_credits(email) == 1
        svc.release_show_credit(key)
        assert not svc.show_credit_already_processed(key)
        assert svc.count_recent_show_credits(email) == 0
