"""Unit tests for near-real-time failure alerting (incident-hardening D1).

Covers the universal-net behaviour the spike-based error monitor can't provide:
novel error signatures always alert, repeats within the window collapse, test/dev
failures are skipped, and — critically — nothing here ever raises into the status
write that triggers it.
"""

from types import SimpleNamespace

import pytest

from backend.services import ops_alerts


# ---------------------------------------------------------------------------
# In-memory Firestore test double
# ---------------------------------------------------------------------------

class _FakeSnap:
    def __init__(self, data):
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class _FakeDoc:
    def __init__(self, store, key):
        self._store = store
        self._key = key

    def get(self):
        return _FakeSnap(self._store.get(self._key))

    def set(self, data):
        self._store[self._key] = dict(data)

    def update(self, data):
        self._store.setdefault(self._key, {}).update(data)


class _FakeCollection:
    def __init__(self, store):
        self._store = store

    def document(self, doc_id):
        return _FakeDoc(self._store, doc_id)


class FakeFirestore:
    """Mimics the subset of google.cloud.firestore.Client used by ops_alerts."""

    def __init__(self, jobs=None):
        self._collections = {}
        # Seed the jobs collection so enrichment reads succeed.
        jobs_store = {}
        for job_id, doc in (jobs or {}).items():
            jobs_store[job_id] = doc
        self._collections["jobs"] = jobs_store

    def collection(self, name):
        return _FakeCollection(self._collections.setdefault(name, {}))


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("FAILURE_ALERTS_ENABLED", "true")
    # Deterministic throttle window for tests.
    monkeypatch.setenv("FAILURE_ALERT_THROTTLE_MINUTES", "30")
    monkeypatch.delenv("FAILURE_ALERT_SKIP_ENVIRONMENTS", raising=False)


@pytest.fixture
def captured(monkeypatch):
    """Capture send_ops_alert calls instead of hitting Discord."""
    sent = []
    monkeypatch.setattr(ops_alerts, "send_ops_alert", lambda msg: sent.append(msg) or True)
    return sent


def _job(environment="production", artist="A", title="B", brand="NOMAD-1632", error_message="boom"):
    return {
        "artist": artist,
        "title": title,
        "request_metadata": {"environment": environment},
        "state_data": {"brand_code": brand},
        "error_message": error_message,
    }


# ---------------------------------------------------------------------------
# send_ops_alert gating
# ---------------------------------------------------------------------------

def test_send_ops_alert_disabled_returns_false(monkeypatch):
    monkeypatch.delenv("FAILURE_ALERTS_ENABLED", raising=False)
    monkeypatch.setattr(ops_alerts, "_is_enabled", lambda: False)
    assert ops_alerts.send_ops_alert("hi") is False


def test_send_ops_alert_no_webhook_returns_false(monkeypatch):
    monkeypatch.setattr(ops_alerts, "_is_enabled", lambda: True)
    monkeypatch.setattr(ops_alerts, "_get_webhook_url", lambda: None)
    assert ops_alerts.send_ops_alert("hi") is False


# ---------------------------------------------------------------------------
# notify_job_failed
# ---------------------------------------------------------------------------

def test_novel_signature_alerts(enabled, captured):
    db = FakeFirestore(jobs={"job-1": _job(error_message="never seen this")})
    sent = ops_alerts.notify_job_failed(db, "jobs", "job-1", message="never seen this")
    assert sent is True
    assert len(captured) == 1
    assert "First time" in captured[0]
    assert "job-1" in captured[0]


def test_repeat_within_window_is_suppressed(enabled, captured):
    db = FakeFirestore(jobs={
        "job-1": _job(),
        "job-2": _job(),
    })
    # Same error string -> same signature.
    ops_alerts.notify_job_failed(db, "jobs", "job-1", additional_fields={"error_message": "same err"})
    ops_alerts.notify_job_failed(db, "jobs", "job-2", additional_fields={"error_message": "same err"})
    assert len(captured) == 1  # second collapsed


def test_distinct_signatures_each_alert(enabled, captured):
    db = FakeFirestore(jobs={"j1": _job(), "j2": _job()})
    ops_alerts.notify_job_failed(db, "jobs", "j1", additional_fields={"error_message": "error one"})
    ops_alerts.notify_job_failed(db, "jobs", "j2", additional_fields={"error_message": "totally different"})
    assert len(captured) == 2


def test_test_environment_skipped(enabled, captured):
    db = FakeFirestore(jobs={"job-t": _job(environment="test")})
    sent = ops_alerts.notify_job_failed(db, "jobs", "job-t", message="boom")
    assert sent is False
    assert captured == []


def test_disabled_environment_no_alert(monkeypatch, captured):
    monkeypatch.delenv("FAILURE_ALERTS_ENABLED", raising=False)
    monkeypatch.setattr(ops_alerts, "_is_enabled", lambda: False)
    db = FakeFirestore(jobs={"job-1": _job()})
    assert ops_alerts.notify_job_failed(db, "jobs", "job-1", message="boom") is False
    assert captured == []


def test_best_effort_never_raises_on_broken_db(enabled, captured):
    class BrokenDB:
        def collection(self, name):
            raise RuntimeError("firestore exploded")

    # Must not propagate. Enrichment + dedup both fail, so it fails OPEN and
    # still sends (missing a real outage is worse than an unenriched alert).
    result = ops_alerts.notify_job_failed(BrokenDB(), "jobs", "job-x", message="boom")
    assert result is True
    assert len(captured) == 1


def test_fail_open_when_dedup_read_errors(enabled, captured, monkeypatch):
    """If the dedup lookup fails we should still alert (missing an outage is worse)."""
    db = FakeFirestore(jobs={"job-1": _job()})
    monkeypatch.setattr(ops_alerts, "_should_alert", lambda *a, **k: (True, False, 0, None))
    assert ops_alerts.notify_job_failed(db, "jobs", "job-1", message="boom") is True
    assert len(captured) == 1


def test_failed_delivery_does_not_suppress_next(enabled, monkeypatch):
    """A failed Discord send must NOT advance the throttle window."""
    db = FakeFirestore(jobs={"job-1": _job(), "job-2": _job()})
    calls = []

    def flaky_send(msg):
        calls.append(msg)
        return len(calls) > 1  # first send fails, second succeeds

    monkeypatch.setattr(ops_alerts, "send_ops_alert", flaky_send)
    # First failure: send attempted but delivery fails → not acked.
    assert ops_alerts.notify_job_failed(db, "jobs", "job-1", additional_fields={"error_message": "same err"}) is False
    # Second failure, same signature, within window: because the first never
    # acked, this must still be attempted (not throttled away) and now succeed.
    assert ops_alerts.notify_job_failed(db, "jobs", "job-2", additional_fields={"error_message": "same err"}) is True
    assert len(calls) == 2
