"""
Tests for divebar_mirror/main.py — focused on the downstream-job chaining that
fixes the "Refresh catalog" race.

The index build, on completion, force-runs the index-dependent scheduler jobs
(file-sync VM + xref rebuild). Firing them here — at the index's actual
completion — rather than concurrently from the caller ensures a just-published
track is byte-synced to GCS on the same refresh instead of racing the sync VM
ahead of the index.
"""
import os
import sys
from unittest.mock import MagicMock

import pytest

# Stub Cloud Function deps not installed in the test env. functions_framework.http
# must be an identity decorator so the real entry point stays callable. The local
# helper modules (drive/index/parser) pull heavy Google client libs at import; stub
# them since these tests only exercise _trigger_downstream_jobs.
_functions_framework = MagicMock()
_functions_framework.http = lambda fn: fn
_scheduler_mod = MagicMock()
for name, mod in (
    ("functions_framework", _functions_framework),
    ("google.cloud.bigquery", MagicMock()),
    ("google.cloud.scheduler_v1", _scheduler_mod),
    ("drive_client", MagicMock()),
    ("filename_parser", MagicMock()),
    ("index_builder", MagicMock()),
):
    sys.modules.setdefault(name, mod)

sys.path.insert(0, os.path.dirname(__file__))

import main  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_scheduler():
    """Fresh scheduler client mock for each test."""
    client = MagicMock()
    _scheduler_mod.CloudSchedulerClient = MagicMock(return_value=client)
    yield client


def test_triggers_both_index_dependent_jobs():
    # autouse _reset_scheduler fixture patches CloudSchedulerClient (returns success)
    result = main._trigger_downstream_jobs()

    assert result["triggered"] == [
        "divebar-sync-vm-daily",
        "divebar-xref-rebuild-daily",
    ]
    assert result["failed"] == []


def test_downstream_job_paths_are_fully_qualified(_reset_scheduler):
    main._trigger_downstream_jobs()
    called = [c.kwargs["name"] for c in _reset_scheduler.run_job.call_args_list]
    assert called == [
        f"projects/{main.GCP_PROJECT_ID}/locations/{main.GCP_REGION}/jobs/divebar-sync-vm-daily",
        f"projects/{main.GCP_PROJECT_ID}/locations/{main.GCP_REGION}/jobs/divebar-xref-rebuild-daily",
    ]


def test_one_downstream_failure_does_not_block_the_other(_reset_scheduler):
    _reset_scheduler.run_job.side_effect = [RuntimeError("already running"), None]

    result = main._trigger_downstream_jobs()

    assert result["triggered"] == ["divebar-xref-rebuild-daily"]
    assert len(result["failed"]) == 1
    assert result["failed"][0]["job"] == "divebar-sync-vm-daily"


def test_index_dependent_jobs_are_the_ones_removed_from_refresh():
    # These must be chained here, NOT triggered concurrently by the lookup refresh.
    assert main.DOWNSTREAM_SCHEDULER_JOBS == [
        "divebar-sync-vm-daily",
        "divebar-xref-rebuild-daily",
    ]


class _Req:
    """Minimal stand-in for a functions_framework request."""

    def __init__(self, json=None, args=None):
        self._json = json
        self.args = args or {}

    def get_json(self, silent=False):
        return self._json


def test_wants_chain_true_from_json_body():
    assert main._wants_downstream_chain(_Req(json={"chain_downstream": True})) is True


def test_wants_chain_from_query_param():
    assert main._wants_downstream_chain(_Req(args={"chain_downstream": "true"})) is True


def test_does_not_chain_when_flag_absent():
    # The nightly mirror cron sends no flag -> must NOT chain (else it double-runs the
    # standalone nightly sync/xref schedules).
    assert main._wants_downstream_chain(_Req(json={})) is False
    assert main._wants_downstream_chain(_Req(json=None)) is False
    assert main._wants_downstream_chain(_Req(json={"chain_downstream": False})) is False
