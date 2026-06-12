"""
Tests for divebar_lookup/main.py — focused on the on-demand `refresh` action.

`refresh` force-runs the divebar pipeline scheduler jobs (mirror index, GCS
file sync, xref rebuild) so a just-published track shows up without waiting for
the nightly runs. The endpoint is otherwise public, so the action is gated by a
shared bearer token (constant-time compare).
"""
import os
import sys
import json
from unittest.mock import MagicMock

import pytest

# Stub out Cloud Function deps that aren't installed in the test env. The
# scheduler/secret clients are imported lazily inside the refresh helpers, so
# stub them here too. functions_framework.http must be an identity decorator so
# the real entry point stays callable (a bare MagicMock would replace it).
_functions_framework = MagicMock()
_functions_framework.http = lambda fn: fn
_scheduler_mod = MagicMock()
for name, mod in (
    ("functions_framework", _functions_framework),
    ("google.cloud.bigquery", MagicMock()),
    ("google.cloud.storage", MagicMock()),
    ("google.cloud.scheduler_v1", _scheduler_mod),
    ("google.cloud.secretmanager", MagicMock()),
):
    sys.modules.setdefault(name, mod)

sys.path.insert(0, os.path.dirname(__file__))

import main  # noqa: E402

TOKEN = "s3cret-refresh-token"


@pytest.fixture(autouse=True)
def _reset_scheduler(monkeypatch):
    """Fresh scheduler client mock + a configured token (read from Secret
    Manager at runtime, mocked here) for each test."""
    monkeypatch.setattr(main, "_get_expected_token", lambda: TOKEN)
    client = MagicMock()
    _scheduler_mod.CloudSchedulerClient = MagicMock(return_value=client)
    yield client


class MockRequest:
    def __init__(self, body, method="POST"):
        self.method = method
        self._body = body

    def get_json(self, silent=False):
        return self._body


# ---------------------------------------------------------------------------
# _refresh — token gate
# ---------------------------------------------------------------------------

class TestRefreshTokenGate:
    def test_wrong_token_raises(self, _reset_scheduler):
        with pytest.raises(PermissionError):
            main._refresh("nope")
        _reset_scheduler.run_job.assert_not_called()

    def test_empty_token_raises(self, _reset_scheduler):
        with pytest.raises(PermissionError):
            main._refresh("")
        _reset_scheduler.run_job.assert_not_called()

    def test_unconfigured_server_token_raises(self, monkeypatch, _reset_scheduler):
        # Secret has no value yet (read returns "") — even if the caller sends a
        # token, an unconfigured server rejects it (fails closed).
        monkeypatch.setattr(main, "_get_expected_token", lambda: "")
        with pytest.raises(PermissionError):
            main._refresh("anything")
        _reset_scheduler.run_job.assert_not_called()


# ---------------------------------------------------------------------------
# _refresh — job triggering
# ---------------------------------------------------------------------------

class TestRefreshTriggers:
    def test_runs_all_pipeline_jobs_in_order(self, _reset_scheduler):
        result = main._refresh(TOKEN)

        assert result["triggered"] == main.REFRESH_SCHEDULER_JOBS
        assert result["failed"] == []
        # Each job referenced by its full path in the configured region.
        called_paths = [c.kwargs["name"] for c in _reset_scheduler.run_job.call_args_list]
        assert called_paths == [
            f"projects/{main.GCP_PROJECT_ID}/locations/{main.GCP_REGION}/jobs/{j}"
            for j in main.REFRESH_SCHEDULER_JOBS
        ]

    def test_one_job_failing_does_not_block_others(self, _reset_scheduler):
        # First job raises (e.g. already running); the rest still fire.
        _reset_scheduler.run_job.side_effect = [
            RuntimeError("already running"), None, None,
        ]
        result = main._refresh(TOKEN)

        assert result["triggered"] == main.REFRESH_SCHEDULER_JOBS[1:]
        assert len(result["failed"]) == 1
        assert result["failed"][0]["job"] == main.REFRESH_SCHEDULER_JOBS[0]


# ---------------------------------------------------------------------------
# divebar_lookup dispatch — refresh action
# ---------------------------------------------------------------------------

class TestRefreshDispatch:
    def test_good_token_returns_200(self, _reset_scheduler):
        body, status, _ = main.divebar_lookup(
            MockRequest({"action": "refresh", "token": TOKEN})
        )
        assert status == 200
        payload = json.loads(body)
        assert payload["status"] == "ok"
        assert payload["triggered"] == main.REFRESH_SCHEDULER_JOBS

    def test_bad_token_returns_403_uniform_message(self, _reset_scheduler):
        body, status, _ = main.divebar_lookup(
            MockRequest({"action": "refresh", "token": "wrong"})
        )
        assert status == 403
        payload = json.loads(body)
        assert payload["status"] == "error"
        # Uniform message — doesn't leak whether the token is configured.
        assert payload["message"] == "forbidden"
        _reset_scheduler.run_job.assert_not_called()

    def test_missing_token_returns_403(self, _reset_scheduler):
        body, status, _ = main.divebar_lookup(MockRequest({"action": "refresh"}))
        assert status == 403
        _reset_scheduler.run_job.assert_not_called()
