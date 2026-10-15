"""Unit tests for LoginCodeStore (emailed 6-digit sign-in codes).

Runs the real store over the in-memory Firestore fake shared with the kjbox
partner tests (writes applied immediately; the store reads before writing).
The Firestore primitives themselves (Increment, create()-only markers, real
transactions) are covered against the emulator in
backend/tests/emulator/test_login_code_store.py.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from backend.services import login_code_store as lcs
from backend.services.login_code_store import (
    CODE_EXPIRY_MINUTES,
    MAX_VERIFY_ATTEMPTS,
    CodeThrottled,
    LoginCodeStore,
    TransactionContention,
    VerifyOutcome,
    code_doc_id,
)
from backend.tests.test_kjbox_partner_api import FakeDb, FakeTransaction

EMAIL = "user@example.com"
CODES, USES, IPS = "login_codes", "login_code_uses", "login_code_ip_sends"


@pytest.fixture
def db():
    return FakeDb()


@pytest.fixture
def store(db):
    def runner(fn):
        db.txn_count += 1
        return fn(FakeTransaction(db.txn_writes))

    return LoginCodeStore(db, "pepper", CODES, USES, transaction_runner=runner, ip_collection=IPS)


def _issue(store, email=EMAIL, **over):
    kwargs = dict(max_per_hour=5, needs_credit_eval=True)
    kwargs.update(over)
    return store.issue_code(email, **kwargs)


def _wrong(code):
    return f"{(int(code) + 1) % 1_000_000:06d}"


def _doc(db, email=EMAIL):
    return db.collection(CODES).docs[code_doc_id(email)]


class TestIssue:
    def test_issues_six_digit_code_stored_only_as_hmac(self, store, db):
        issued = _issue(store, fields={"locale": "de", "ip_address": "1.2.3.4"})
        assert len(issued.code) == 6 and issued.code.isdigit()
        assert issued.doc_id == hashlib.sha256(EMAIL.encode()).hexdigest()
        doc = _doc(db)
        assert issued.code not in str(doc.values())
        assert doc["code_hash"] == store.hash_code(doc["code_id"], issued.code)
        assert doc["attempts"] == 0 and doc["email"] == EMAIL
        assert doc["expires_at"] - doc["created_at"] == timedelta(minutes=CODE_EXPIRY_MINUTES)
        # Caller metadata rides on the record
        assert doc["locale"] == "de" and doc["ip_address"] == "1.2.3.4"

    def test_email_is_normalised(self, store, db):
        _issue(store, email="  User@Example.COM ")
        assert code_doc_id(EMAIL) in db.collection(CODES).docs

    def test_pepper_changes_hash(self, db):
        a = LoginCodeStore(db, "p1", CODES, USES)
        b = LoginCodeStore(db, "p2", CODES, USES)
        assert a.hash_code("id", "123456") != b.hash_code("id", "123456")

    def test_reissue_invalidates_previous_code(self, store):
        first = _issue(store)
        second = _issue(store)
        if first.code != second.code:
            assert store.verify_code(EMAIL, first.code)[0] == VerifyOutcome.INVALID
        assert store.verify_code(EMAIL, second.code)[0] == VerifyOutcome.OK

    def test_per_email_throttle(self, store, db):
        for _ in range(3):
            _issue(store, max_per_hour=3)
        with pytest.raises(CodeThrottled):
            _issue(store, max_per_hour=3)
        assert len(_doc(db)["send_times"]) == 3

    def test_old_sends_fall_out_of_rolling_window(self, store, db):
        _issue(store)
        _doc(db)["send_times"] = [datetime.now(timezone.utc) - timedelta(hours=2)] * 5
        _issue(store, max_per_hour=5)  # old sends don't count
        assert len(_doc(db)["send_times"]) == 1

    def test_per_ip_throttle_spans_emails(self, store, db):
        for i in range(2):
            _issue(store, email=f"u{i}@example.com", throttle_ip="9.9.9.9", max_per_ip_per_hour=2)
        with pytest.raises(CodeThrottled):
            _issue(store, email="u2@example.com", throttle_ip="9.9.9.9", max_per_ip_per_hour=2)
        # Throttled request wrote nothing (no code for u2)
        assert code_doc_id("u2@example.com") not in db.collection(CODES).docs
        # A different IP is unaffected
        _issue(store, email="u2@example.com", throttle_ip="8.8.8.8", max_per_ip_per_hour=2)
        # IP stored only as a hash (doc id), never raw
        ip_docs = db.collection(IPS).docs
        assert hashlib.sha256(b"9.9.9.9").hexdigest() in ip_docs
        assert "9.9.9.9" not in str(ip_docs)

    def test_ip_and_code_written_in_one_transaction(self, store, db):
        _issue(store, throttle_ip="9.9.9.9", max_per_ip_per_hour=5)
        assert db.txn_count == 1
        assert sorted(w[1] is db.collection(IPS) for w in db.txn_writes) == [False, True]

    def test_no_ip_write_without_throttle_ip(self, store, db):
        _issue(store)
        assert db.collection(IPS).docs == {}
        assert len(db.txn_writes) == 1

    def test_throttle_ip_requires_ip_collection(self, db):
        s = LoginCodeStore(db, "p", CODES, USES, transaction_runner=lambda fn: fn(FakeTransaction([])))
        with pytest.raises(ValueError):
            _issue(s, throttle_ip="1.1.1.1", max_per_ip_per_hour=1)

    def test_needs_credit_eval_false_once_eval_stored(self, store, db):
        assert _issue(store).needs_credit_eval is True
        _doc(db)["credit_eval_decision"] = "grant"
        second = _issue(store)
        assert second.needs_credit_eval is False
        assert _doc(db)["credit_eval_decision"] == "grant"  # merge keeps it
        assert _issue(store, needs_credit_eval=False).needs_credit_eval is False


class TestVerify:
    def test_round_trip_and_single_use(self, store, db):
        issued = _issue(store, fields={"locale": "es"})
        outcome, record = store.verify_code(EMAIL, issued.code)
        assert outcome == VerifyOutcome.OK and record["locale"] == "es"
        assert _doc(db)["code_hash"] is None
        assert store.verify_code(EMAIL, issued.code)[0] == VerifyOutcome.EXPIRED

    def test_unknown_email_is_expired(self, store):
        assert store.verify_code("nobody@example.com", "123456") == (VerifyOutcome.EXPIRED, None)

    def test_wrong_code_invalid(self, store):
        issued = _issue(store)
        assert store.verify_code(EMAIL, _wrong(issued.code)) == (VerifyOutcome.INVALID, None)

    @pytest.mark.parametrize("bad", ["", "12345", "1234567", "abcdef", " "])
    def test_malformed_code_invalid(self, store, bad):
        _issue(store)
        assert store.verify_code(EMAIL, bad)[0] == VerifyOutcome.INVALID

    def test_whitespace_around_code_tolerated(self, store):
        issued = _issue(store)
        assert store.verify_code(EMAIL, f" {issued.code} ")[0] == VerifyOutcome.OK

    def test_expired_code_burned(self, store, db):
        issued = _issue(store)
        _doc(db)["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        assert store.verify_code(EMAIL, issued.code)[0] == VerifyOutcome.EXPIRED
        assert _doc(db)["code_hash"] is None

    def test_attempt_cap_then_burned(self, store, db):
        issued = _issue(store)
        for _ in range(MAX_VERIFY_ATTEMPTS):
            assert store.verify_code(EMAIL, _wrong(issued.code))[0] == VerifyOutcome.INVALID
        assert store.verify_code(EMAIL, issued.code)[0] == VerifyOutcome.TOO_MANY_ATTEMPTS
        assert _doc(db)["code_hash"] is None
        assert store.verify_code(EMAIL, issued.code)[0] == VerifyOutcome.EXPIRED

    def test_use_marker_blocks_replay(self, store, db):
        issued = _issue(store)
        db.collection(USES).document(_doc(db)["code_id"]).create({"email": EMAIL})
        assert store.verify_code(EMAIL, issued.code)[0] == VerifyOutcome.EXPIRED

    def test_collections_are_isolated(self, db):
        runner = lambda fn: fn(FakeTransaction([]))  # noqa: E731
        gen = LoginCodeStore(db, "p", CODES, USES, transaction_runner=runner)
        kj = LoginCodeStore(db, "p", "kjbox_login_codes", "kjbox_login_code_uses", transaction_runner=runner)
        issued = gen.issue_code(EMAIL, max_per_hour=5, needs_credit_eval=False)
        assert kj.verify_code(EMAIL, issued.code)[0] == VerifyOutcome.EXPIRED
        assert gen.verify_code(EMAIL, issued.code)[0] == VerifyOutcome.OK


class TestRealRunner:
    def test_maps_firestore_giveup_to_contention(self, monkeypatch):
        def give_up(fn):
            def wrapped(transaction):
                raise ValueError("Failed to commit transaction in 10 attempts.")
            return wrapped

        monkeypatch.setattr(lcs.firestore, "transactional", give_up)
        store = LoginCodeStore(MagicMock(), "p", CODES, USES)
        with pytest.raises(TransactionContention):
            store.issue_code(EMAIL, max_per_hour=5, needs_credit_eval=False)

    def test_other_value_errors_propagate(self, monkeypatch):
        def boom(fn):
            def wrapped(transaction):
                raise ValueError("something else")
            return wrapped

        monkeypatch.setattr(lcs.firestore, "transactional", boom)
        with pytest.raises(ValueError, match="something else"):
            lcs.run_firestore_transaction(MagicMock(), lambda t: None)
