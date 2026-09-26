"""
Emulator integration tests for the kjbox partner service.

Runs KjboxPartnerService against a REAL Firestore emulator so the primitives
the security properties rest on are verified for real, not faked: the atomic
Increment attempt counter, create()-only single-use / idempotency markers,
set(merge=True) preserving the precomputed eval, and the FieldFilter queries
behind the signup / show-credit caps. Run with: scripts/run-emulator-tests.sh
"""
import uuid
from concurrent.futures import ThreadPoolExecutor
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
    kwargs = dict(max_per_hour=5, needs_credit_eval=True, locale="en", ui_locale="en",
                  venue="Dive", ip_address=None, user_agent=None)
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
        with pytest.raises(kps.CodeThrottled):
            _issue(svc, email, max_per_hour=2)
        assert svc.verify_code(email, second.code)[0] == kps.VerifyOutcome.OK

    def test_throttle_holds_under_concurrency(self, svc):
        """Throttle check + send_times append + code write are one transaction."""
        email = _email()

        def attempt(_):
            try:
                _issue(svc, email, max_per_hour=5)
                return True
            except (kps.CodeThrottled, kps.TransactionContention):
                return False

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(12)))
        # Never more than the cap, and every success is recorded exactly once
        assert 1 <= sum(results) <= 5
        assert len(svc.get_code_record(email)["send_times"]) == sum(results)
        # Sequential top-up reaches exactly the cap, then throttles
        while len(svc.get_code_record(email)["send_times"]) < 5:
            _issue(svc, email, max_per_hour=5)
        with pytest.raises(kps.CodeThrottled):
            _issue(svc, email, max_per_hour=5)

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



def _seed_user(svc, credits=0):
    email = _email()
    ref = svc.db.collection("gen_users").document(email)
    ref.set({"email": email, "credits": credits, "credit_transactions": []})
    return email, ref


def _grant(svc, ref, email, key, **over):
    kwargs = dict(venue="Dive", only_if_empty=False, max_per_24h=5)
    kwargs.update(over)
    return svc.grant_show_credit(ref, email, key, **kwargs)


class TestShowCredit:
    def test_grant_idempotent_and_recorded(self, svc):
        email, ref = _seed_user(svc, credits=0)
        key = uuid.uuid4().hex
        assert _grant(svc, ref, email, key) == kps.ShowCreditResult(granted=True, credits=1)
        assert _grant(svc, ref, email, key) == kps.ShowCreditResult(granted=False, credits=1)
        data = ref.get().to_dict()
        assert data["credits"] == 1
        assert data["credit_transactions"][-1]["reason"] == "kjbox show credit"

    def test_only_if_empty_does_not_claim_key(self, svc):
        email, ref = _seed_user(svc, credits=1)
        key = uuid.uuid4().hex
        assert _grant(svc, ref, email, key, only_if_empty=True).granted is False
        assert _grant(svc, ref, email, key).granted is True
        assert ref.get().to_dict()["credits"] == 2

    def test_cap_and_credits_hold_under_concurrency(self, svc):
        email, ref = _seed_user(svc, credits=0)

        prefix = uuid.uuid4().hex

        def attempt(i):
            try:
                return _grant(svc, ref, email, f"{prefix}-{i}", max_per_24h=3).granted
            except (kps.ShowCreditCapReached, kps.TransactionContention):
                return False

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(8)))
        granted = sum(results)
        assert 1 <= granted <= 3
        # Credits exactly match grants (no lost updates, no over-grant)
        assert ref.get().to_dict()["credits"] == granted

    def test_same_key_concurrently_grants_once(self, svc):
        email, ref = _seed_user(svc, credits=0)
        with ThreadPoolExecutor(max_workers=6) as pool:
            key = uuid.uuid4().hex

            def attempt(_):
                try:
                    return _grant(svc, ref, email, key).granted
                except kps.TransactionContention:
                    return False

            results = list(pool.map(attempt, range(6)))
        assert sum(results) == 1
        assert ref.get().to_dict()["credits"] == 1
