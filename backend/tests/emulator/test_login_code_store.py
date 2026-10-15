"""
Emulator integration tests for LoginCodeStore (gen email-code sign-in).

Runs the store against a REAL Firestore emulator with gen's own collections so
the primitives behind the security properties are verified for real: the
atomic Increment attempt counter, the create()-only single-use marker, and the
per-email + per-IP throttles held inside one real transaction under
concurrency. Run with: scripts/run-emulator-tests.sh
"""
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from google.cloud import firestore

from backend.api.routes.login_code import (
    LOGIN_CODE_IP_COLLECTION,
    LOGIN_CODE_USES_COLLECTION,
    LOGIN_CODES_COLLECTION,
)
from backend.services.login_code_store import (
    MAX_VERIFY_ATTEMPTS,
    CodeThrottled,
    LoginCodeStore,
    TransactionContention,
    VerifyOutcome,
)

from .conftest import emulators_running

pytestmark = pytest.mark.skipif(
    not emulators_running(),
    reason="GCP emulators not running. Start with: scripts/start-emulators.sh",
)


@pytest.fixture
def store():
    return LoginCodeStore(
        firestore.Client(), "emulator-pepper",
        LOGIN_CODES_COLLECTION, LOGIN_CODE_USES_COLLECTION, ip_collection=LOGIN_CODE_IP_COLLECTION,
    )


def _email():
    return f"lc-{uuid.uuid4().hex[:8]}@example.com"


def _ip():
    return f"ip-{uuid.uuid4().hex}"  # only ever hashed into a doc id


def _wrong(code):
    return f"{(int(code) + 1) % 1_000_000:06d}"


def test_round_trip_single_use_and_attempt_cap(store):
    email = _email()
    issued = store.issue_code(email, max_per_hour=5, needs_credit_eval=False, fields={"locale": "es"})
    assert store.verify_code(email, _wrong(issued.code))[0] == VerifyOutcome.INVALID
    outcome, record = store.verify_code(email, issued.code)
    assert outcome == VerifyOutcome.OK and record["locale"] == "es"
    assert store.verify_code(email, issued.code)[0] == VerifyOutcome.EXPIRED

    issued = store.issue_code(email, max_per_hour=5, needs_credit_eval=False)
    for _ in range(MAX_VERIFY_ATTEMPTS):
        assert store.verify_code(email, _wrong(issued.code))[0] == VerifyOutcome.INVALID
    assert store.verify_code(email, issued.code)[0] == VerifyOutcome.TOO_MANY_ATTEMPTS


def test_per_ip_throttle_holds_under_concurrency(store):
    """Per-IP check + append + code write are one transaction, across emails."""
    ip = _ip()

    def attempt(_):
        try:
            store.issue_code(_email(), max_per_hour=5, needs_credit_eval=False,
                             throttle_ip=ip, max_per_ip_per_hour=4)
            return True
        except (CodeThrottled, TransactionContention):
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(10)))
    assert 1 <= sum(results) <= 4
    # Sequential top-up reaches exactly the cap, then throttles
    for _ in range(4 - sum(results)):
        store.issue_code(_email(), max_per_hour=5, needs_credit_eval=False,
                         throttle_ip=ip, max_per_ip_per_hour=4)
    with pytest.raises(CodeThrottled):
        store.issue_code(_email(), max_per_hour=5, needs_credit_eval=False,
                         throttle_ip=ip, max_per_ip_per_hour=4)
