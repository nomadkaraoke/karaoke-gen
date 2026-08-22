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

    def test_refresh_triggers_only_the_refresh_mirror_not_sync_or_xref(self, _reset_scheduler):
        # Regression: the file-sync VM and xref rebuild are chained by the index
        # function on completion (see divebar_mirror._trigger_downstream_jobs), NOT
        # fired here. Firing them here concurrently raced the sync VM ahead of the
        # index, so a just-published track was indexed but never byte-synced to GCS
        # until the next nightly run. Refresh must fire ONLY the flag-carrying mirror
        # trigger, which chains the rest.
        assert main.REFRESH_SCHEDULER_JOBS == ["divebar-mirror-refresh"]
        assert "divebar-sync-vm-daily" not in main.REFRESH_SCHEDULER_JOBS
        assert "divebar-xref-rebuild-daily" not in main.REFRESH_SCHEDULER_JOBS
        # Must NOT use the nightly cron job (which omits the chain flag).
        assert "divebar-mirror-daily" not in main.REFRESH_SCHEDULER_JOBS

        main._refresh(TOKEN)
        called_paths = [c.kwargs["name"] for c in _reset_scheduler.run_job.call_args_list]
        assert all("divebar-sync-vm-daily" not in p for p in called_paths)
        assert all("divebar-xref-rebuild-daily" not in p for p in called_paths)
        assert any("divebar-mirror-refresh" in p for p in called_paths)


# ---------------------------------------------------------------------------
# _norm_sql — symmetric normalization for the xref join
# ---------------------------------------------------------------------------

class TestNormSql:
    def test_embeds_column_and_is_deterministic(self):
        a = main._norm_sql("kn.Artist")
        assert "kn.Artist" in a
        assert main._norm_sql("kn.Artist") == a  # pure / deterministic

    def test_replicates_normalize_for_search_steps(self):
        expr = main._norm_sql("db.title")
        # diacritics (NFD + drop combining marks), lower, leading "the", and the
        # unicode-aware punctuation strip must all be present.
        assert "NORMALIZE(COALESCE(db.title" in expr
        assert "NFD" in expr and r"\p{Mn}" in expr
        assert "LOWER(" in expr
        assert r"'^the '" in expr
        assert r"\p{L}" in expr and r"\p{N}" in expr

    def test_both_sides_use_same_expression(self):
        # The whole point of the fix: KN and Divebar sides normalize identically.
        kn = main._norm_sql("X")
        db = main._norm_sql("X")
        assert kn == db


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


def _stats_row(**overrides):
    """A fake BigQuery result row for _get_full_stats (attribute access)."""
    defaults = dict(
        total_files=50249, total_brands=63, with_metadata=50072,
        total_gb=876.4, gcs_synced=50031, gcs_pending=0, gcs_unavailable=218,
        gcs_synced_gb=874.7, gcs_pending_gb=0.0, gcs_unavailable_gb=1.6,
        last_index_sync=None, total_matches=38435, unique_kn_songs=1,
        unique_divebar_files=1, last_xref_rebuild=None, kn_songs=1, kn_community=1,
    )
    defaults.update(overrides)
    row = MagicMock()
    for k, v in defaults.items():
        setattr(row, k, v)
    return row


def _patch_bq(monkeypatch, row):
    """Make main.bigquery.Client().query().result() yield [row] then [] (formats)."""
    client = MagicMock()
    client.query.return_value.result.side_effect = [[row], []]
    monkeypatch.setattr(main.bigquery, "Client", lambda project=None: client)


class TestFullStatsPercent:
    def test_percent_100_when_no_pending(self, monkeypatch):
        # All syncable files mirrored; the only non-synced rows are unavailable.
        _patch_bq(monkeypatch, _stats_row(gcs_synced=50031, gcs_pending=0, gcs_unavailable=218))
        g = main._get_full_stats()["gcs_mirror"]
        assert g["pending"] == 0
        assert g["unavailable"] == 218
        assert g["syncable_total"] == 50031
        assert g["percent"] == 100.0  # green

    def test_pending_keeps_percent_below_100(self, monkeypatch):
        # Real pending work (null gcs_path) legitimately holds it under 100.
        _patch_bq(monkeypatch, _stats_row(gcs_synced=50031, gcs_pending=217, gcs_unavailable=1))
        g = main._get_full_stats()["gcs_mirror"]
        assert g["pending"] == 217
        assert g["percent"] == 99.6

    def test_unavailable_excluded_from_denominator(self, monkeypatch):
        # 90 synced, 10 unavailable, 0 pending -> 100% (not 90%).
        _patch_bq(monkeypatch, _stats_row(total_files=100, gcs_synced=90, gcs_pending=0, gcs_unavailable=10))
        g = main._get_full_stats()["gcs_mirror"]
        assert g["syncable_total"] == 90
        assert g["percent"] == 100.0

    def test_zero_syncable_is_zero_not_crash(self, monkeypatch):
        _patch_bq(monkeypatch, _stats_row(total_files=5, gcs_synced=0, gcs_pending=0, gcs_unavailable=5))
        g = main._get_full_stats()["gcs_mirror"]
        assert g["percent"] == 0
