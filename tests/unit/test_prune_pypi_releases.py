"""Unit tests for the PyPI retention-policy planner (scripts/prune_pypi_releases.py).

The script isn't part of the installed package, so we load it by path.
"""

import datetime
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "prune_pypi_releases.py"
_spec = importlib.util.spec_from_file_location("prune_pypi_releases", _SCRIPT)
prune = importlib.util.module_from_spec(_spec)
# Register before exec so @dataclass can resolve the module via sys.modules.
sys.modules[_spec.name] = prune
_spec.loader.exec_module(prune)


def _release(date: str, size: int = 62_000_000) -> list[dict]:
    """A one-file release uploaded on ``date`` (YYYY-MM-DD)."""
    return [{"size": size, "upload_time_iso_8601": f"{date}T12:00:00.000000Z"}]


NOW = datetime.date(2026, 8, 29)


def test_recent_releases_are_all_kept():
    releases = {
        "1.0.0": _release("2026-08-01"),  # 28 days old
        "1.0.1": _release("2026-08-20"),  # 9 days old
        "1.0.2": _release("2026-08-29"),  # today
    }
    plan = prune.compute_plan(releases, keep_days=60, now=NOW)
    assert set(plan.keep) == {"1.0.0", "1.0.1", "1.0.2"}
    assert plan.delete == []


def test_old_month_keeps_only_its_latest():
    releases = {
        # All well outside the 60-day window (Jan 2026).
        "0.1.0": _release("2026-01-05"),
        "0.1.1": _release("2026-01-15"),
        "0.1.2": _release("2026-01-28"),  # newest in January -> kept
        # A newer release to be the overall-latest so January isn't protected.
        "0.9.0": _release("2026-08-29"),
    }
    plan = prune.compute_plan(releases, keep_days=60, now=NOW)
    assert "0.1.2" in plan.keep  # newest of the old month
    assert set(plan.delete) == {"0.1.0", "0.1.1"}


def test_one_keeper_per_distinct_old_month():
    releases = {
        "0.1.0": _release("2026-01-10"),
        "0.1.1": _release("2026-01-20"),  # Jan keeper
        "0.2.0": _release("2026-02-10"),
        "0.2.1": _release("2026-02-25"),  # Feb keeper
        "9.9.9": _release("2026-08-29"),  # latest overall
    }
    plan = prune.compute_plan(releases, keep_days=60, now=NOW)
    assert "0.1.1" in plan.keep and "0.2.1" in plan.keep
    assert set(plan.delete) == {"0.1.0", "0.2.0"}


def test_latest_overall_always_kept_even_if_ancient_and_alone_in_month():
    # A single very old release is both "latest overall" and "newest in month".
    releases = {"0.0.1": _release("2024-01-01")}
    plan = prune.compute_plan(releases, keep_days=60, now=NOW)
    assert plan.keep == ["0.0.1"]
    assert plan.delete == []


def test_sizes_and_reclaim_math():
    releases = {
        "0.1.0": _release("2026-01-10", size=100),  # deleted
        "0.1.1": _release("2026-01-20", size=200),  # Jan keeper
        "9.9.9": _release("2026-08-29", size=50),  # latest
    }
    plan = prune.compute_plan(releases, keep_days=60, now=NOW)
    assert plan.current_bytes == 350
    assert plan.delete == ["0.1.0"]
    assert plan.delete_bytes == 100
    assert plan.kept_bytes == 250


def test_empty_and_undated_releases_ignored():
    releases = {
        "0.1.0": [],  # fully yanked -> no files
        "0.1.1": [{"size": 5}],  # no upload_time -> undated, skipped
        "9.9.9": _release("2026-08-29", size=10),
    }
    plan = prune.compute_plan(releases, keep_days=60, now=NOW)
    assert plan.keep == ["9.9.9"]
    assert plan.delete == []
    assert plan.current_bytes == 10  # empty/undated contribute nothing


def test_delete_list_sorted_most_recent_first():
    releases = {
        "0.1.0": _release("2026-01-10"),
        "0.2.0": _release("2026-02-10"),
        "0.3.0": _release("2026-03-10"),
        # keepers: one per month is the only one per month here, so add extras
        "0.1.9": _release("2026-01-11"),
        "0.2.9": _release("2026-02-11"),
        "0.3.9": _release("2026-03-11"),
        "9.9.9": _release("2026-08-29"),
    }
    plan = prune.compute_plan(releases, keep_days=60, now=NOW)
    # Deleted are the earlier-in-month ones; list must be newest-first.
    assert plan.delete == ["0.3.0", "0.2.0", "0.1.0"]


def test_console_js_is_wellformed_and_contains_versions():
    js = prune.render_console_js("karaoke-gen", ["1.2.3", "4.5.6"])
    assert "confirm_delete_version" in js
    assert "csrf_token" in js
    assert json.dumps(["1.2.3", "4.5.6"]) in js
    assert "/manage/project/${project}/release/" in js


def test_render_json_shape():
    releases = {
        "0.1.0": _release("2026-01-05", size=100),  # deleted (superseded in Jan)
        "0.1.5": _release("2026-01-20", size=200),  # January keeper
        "9.9.9": _release("2026-08-29", size=50),  # latest overall
    }
    plan = prune.compute_plan(releases, keep_days=60, now=NOW)
    payload = json.loads(prune.render_json("karaoke-gen", plan))
    assert payload["delete_count"] == 1
    assert payload["delete_versions"] == ["0.1.0"]
    assert payload["cap_gb"] == prune.PYPI_TOTAL_SIZE_CAP_GB
    assert payload["keep_count"] == 2


@pytest.mark.parametrize("keep_days,expected_deleted", [(60, {"0.5.0"}), (400, set())])
def test_keep_days_window_is_configurable(keep_days, expected_deleted):
    releases = {
        "0.5.0": _release("2026-06-01"),  # 89 days old
        "0.5.1": _release("2026-06-15"),  # 75 days old, June keeper
        "9.9.9": _release("2026-08-29"),
    }
    plan = prune.compute_plan(releases, keep_days=keep_days, now=NOW)
    assert set(plan.delete) == expected_deleted
