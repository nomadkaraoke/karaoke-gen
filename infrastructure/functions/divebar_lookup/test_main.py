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


def _kn_row(artist, title, brand, watch):
    row = MagicMock()
    row.Artist, row.Title, row.Brand, row.Watch = artist, title, brand, watch
    return row


class TestDivebarSearch:
    """`search` matches accent-insensitively on both the query and the haystack."""

    def _patch_query(self, monkeypatch, rows):
        captured = {}
        client = MagicMock()

        def _query(sql, job_config=None):
            captured["sql"] = sql
            captured["params"] = job_config.query_parameters if job_config else []
            result = MagicMock()
            result.result.return_value = rows
            return result

        client.query.side_effect = _query
        monkeypatch.setattr(main.bigquery, "Client", lambda project=None: client)
        return captured

    def test_haystack_is_diacritic_folded(self, monkeypatch):
        captured = self._patch_query(monkeypatch, [])
        main._search_divebar("feliz navidad")
        sql = captured["sql"]
        # The artist/title haystack diacritic-folds (NORMALIZE NFD + drop
        # combining marks) so an ASCII query matches "José Feliciano".
        assert "NORMALIZE" in sql and r"\p{Mn}" in sql
        assert "divebar_catalog" in sql

    def test_accented_query_is_folded_before_matching(self, monkeypatch):
        # Symmetric: an accented query must also match unaccented catalog rows.
        # bigquery is a stub module here, so capture the param factory's args.
        from types import SimpleNamespace
        captured = self._patch_query(monkeypatch, [])
        monkeypatch.setattr(
            main.bigquery, "ScalarQueryParameter",
            lambda name, typ, value: (name, typ, value))
        monkeypatch.setattr(
            main.bigquery, "QueryJobConfig",
            lambda **kw: SimpleNamespace(query_parameters=kw.get("query_parameters", [])))
        main._search_divebar("José Feliciano Feliz Navidad")
        pattern = next(p for p in captured["params"] if p[0] == "query_pattern")
        assert pattern[2] == "%jose feliciano feliz navidad%"


class TestKnCommunitySearch:
    """`kn_community_search` reads our own karaokenerds_community — no scraping."""

    def _patch_query(self, monkeypatch, rows):
        """Capture the SQL + params passed to BigQuery and yield `rows`."""
        captured = {}
        client = MagicMock()

        def _query(sql, job_config=None):
            captured["sql"] = sql
            captured["params"] = job_config.query_parameters if job_config else []
            result = MagicMock()
            result.result.return_value = rows
            return result

        client.query.side_effect = _query
        monkeypatch.setattr(main.bigquery, "Client", lambda project=None: client)
        return captured

    def test_returns_flat_rows(self, monkeypatch):
        rows = [
            _kn_row("Fleetwood Mac", "Dreams", "Nomad Karaoke", "https://youtu.be/a"),
            _kn_row("Fleetwood Mac", "Dreams", "WTF Karaoke", "https://youtu.be/b"),
        ]
        self._patch_query(monkeypatch, rows)
        out = main._search_kn_community("fleetwood mac dreams")
        assert out == [
            {"artist": "Fleetwood Mac", "title": "Dreams", "brand": "Nomad Karaoke", "watch": "https://youtu.be/a"},
            {"artist": "Fleetwood Mac", "title": "Dreams", "brand": "WTF Karaoke", "watch": "https://youtu.be/b"},
        ]

    def test_token_and_matching_builds_one_condition_per_token(self, monkeypatch):
        captured = self._patch_query(monkeypatch, [])
        main._search_kn_community("daft punk one more time")
        sql = captured["sql"]
        # 5 tokens -> one literal-substring (STRPOS) condition each, ANDed.
        assert sql.count("STRPOS(hay, @tok") == 5
        assert " AND " in sql
        assert "karaokenerds_community" in sql
        # STRPOS is literal (no LIKE wildcards) so there is nothing to escape.
        assert "LIKE" not in sql and "ESCAPE" not in sql
        # Accent-insensitive: the haystack diacritic-folds (NORMALIZE NFD + drop
        # combining marks) so an ASCII query matches accented catalog values.
        assert "NORMALIZE" in sql and r"\p{Mn}" in sql

    def test_long_tokens_get_fuzzy_edit_distance(self, monkeypatch):
        # Tokens >= 4 chars add an EDIT_DISTANCE fuzzy fallback for typos.
        captured = self._patch_query(monkeypatch, [])
        main._search_kn_community("books boxs")  # both len>=4
        assert captured["sql"].count("EDIT_DISTANCE") == 2

    def test_short_tokens_are_exact_only(self, monkeypatch):
        # Tokens < 4 chars stay exact-substring (no fuzzy, to avoid noise).
        captured = self._patch_query(monkeypatch, [])
        main._search_kn_community("abc")
        assert "STRPOS(hay, @tok0)" in captured["sql"]
        assert "EDIT_DISTANCE" not in captured["sql"]

    def test_metacharacter_token_is_literal(self, monkeypatch):
        # STRPOS treats %/_ literally — no LIKE, no ESCAPE, no crash.
        captured = self._patch_query(monkeypatch, [])
        main._search_kn_community("100%_off")
        assert "STRPOS(hay, @tok0)" in captured["sql"]
        assert "LIKE" not in captured["sql"] and "ESCAPE" not in captured["sql"]

    def test_accented_query_token_is_folded(self, monkeypatch):
        captured = self._patch_query(monkeypatch, [])
        main._search_kn_community("Maxïmo Park")
        assert captured["sql"].count("STRPOS(hay, @tok") == 2
        # Blank-after-fold input still short-circuits without hitting BigQuery.
        monkeypatch.setattr(main.bigquery, "Client",
                            MagicMock(side_effect=AssertionError("BQ should not be called")))
        assert main._search_kn_community("   ") == []

    def test_blank_query_returns_empty_without_bq(self, monkeypatch):
        # No tokens -> never touches BigQuery.
        monkeypatch.setattr(main.bigquery, "Client",
                            MagicMock(side_effect=AssertionError("BQ should not be called")))
        assert main._search_kn_community("   ") == []

    def test_dispatch_returns_results_and_count(self, monkeypatch):
        self._patch_query(monkeypatch, [_kn_row("ABBA", "SOS", "Nomad Karaoke", "https://youtu.be/s")])
        body, status, _ = main.divebar_lookup(
            MockRequest({"action": "kn_community_search", "query": "abba sos"})
        )
        assert status == 200
        payload = json.loads(body)
        assert payload["status"] == "ok"
        assert payload["count"] == 1
        assert payload["results"][0]["brand"] == "Nomad Karaoke"

    def test_dispatch_missing_query_returns_400(self, monkeypatch):
        body, status, _ = main.divebar_lookup(
            MockRequest({"action": "kn_community_search"})
        )
        assert status == 400

    def test_dispatch_null_query_returns_400_not_500(self, monkeypatch):
        # {"query": null} must not crash on .strip() -> generic 500.
        body, status, _ = main.divebar_lookup(
            MockRequest({"action": "kn_community_search", "query": None})
        )
        assert status == 400

    def test_dispatch_non_integer_limit_returns_400(self, monkeypatch):
        for bad in ("50", True, 1.5):
            _body, status, _ = main.divebar_lookup(
                MockRequest({"action": "kn_community_search", "query": "abba", "limit": bad})
            )
            assert status == 400, f"limit={bad!r} should be rejected"

    def test_dispatch_negative_limit_clamped_not_500(self, monkeypatch):
        self._patch_query(monkeypatch, [])
        _body, status, _ = main.divebar_lookup(
            MockRequest({"action": "kn_community_search", "query": "abba", "limit": -5})
        )
        assert status == 200


def _kn_union_row(src, artist, title, brand_info, watch=None):
    row = MagicMock()
    row.src, row.Artist, row.Title, row.brand_info, row.Watch = (
        src, artist, title, brand_info, watch)
    return row


class TestKnSearch:
    """`kn_search` returns community + full-catalog rows from ONE BigQuery job."""

    def _patch_query(self, monkeypatch, rows):
        captured = {}
        client = MagicMock()

        def _query(sql, job_config=None):
            captured["sql"] = sql
            captured["params"] = job_config.query_parameters if job_config else []
            result = MagicMock()
            result.result.return_value = rows
            return result

        client.query.side_effect = _query
        monkeypatch.setattr(main.bigquery, "Client", lambda project=None: client)
        return captured

    def test_splits_rows_by_source(self, monkeypatch):
        rows = [
            _kn_union_row("community", "Tenacious D", "Tribute", "BELLY", "https://youtu.be/j"),
            _kn_union_row("full", "Tenacious D", "Tribute", "BELLY,CK,KV,SF"),
        ]
        self._patch_query(monkeypatch, rows)
        out = main._search_kn_all("tenacious d tribute")
        assert out["community"] == [
            {"artist": "Tenacious D", "title": "Tribute", "brand": "BELLY", "watch": "https://youtu.be/j"},
        ]
        assert out["full"] == [
            {"artist": "Tenacious D", "title": "Tribute", "brands": "BELLY,CK,KV,SF"},
        ]

    def test_queries_both_tables_in_one_job_with_per_source_limit(self, monkeypatch):
        # The whole point: a song with only commercial disc brands (in the raw
        # table, not the community table) must be findable — in a single BQ job,
        # with the limit applied per source so the 300k-row raw table can't
        # starve community rows.
        captured = self._patch_query(monkeypatch, [])
        main._search_kn_all("big green tractor")
        sql = captured["sql"]
        assert "karaokenerds_community" in sql
        assert "karaokenerds_raw" in sql
        assert "UNION ALL" in sql
        assert "QUALIFY" in sql and "PARTITION BY src" in sql

    def test_same_matching_semantics_as_community_search(self, monkeypatch):
        # Both searches share _kn_match_parts — token-AND STRPOS + fuzzy fallback.
        captured = self._patch_query(monkeypatch, [])
        main._search_kn_all("daft punk one more time")
        sql = captured["sql"]
        assert sql.count("STRPOS(hay, @tok") == 5
        assert "LIKE" not in sql and "ESCAPE" not in sql
        assert "NORMALIZE" in sql and r"\p{Mn}" in sql

    def test_blank_query_returns_empty_without_bq(self, monkeypatch):
        monkeypatch.setattr(main.bigquery, "Client",
                            MagicMock(side_effect=AssertionError("BQ should not be called")))
        assert main._search_kn_all("   ") == {"community": [], "full": []}

    def test_dispatch_returns_both_sets_and_count(self, monkeypatch):
        self._patch_query(monkeypatch, [
            _kn_union_row("community", "ABBA", "SOS", "NOMAD", "https://youtu.be/s"),
            _kn_union_row("full", "ABBA", "SOS", "NOMAD,SF,KV"),
        ])
        body, status, _ = main.divebar_lookup(
            MockRequest({"action": "kn_search", "query": "abba sos"})
        )
        assert status == 200
        payload = json.loads(body)
        assert payload["status"] == "ok"
        assert payload["count"] == 2
        assert payload["community"][0]["watch"] == "https://youtu.be/s"
        assert payload["full"][0]["brands"] == "NOMAD,SF,KV"

    def test_dispatch_missing_query_returns_400(self, monkeypatch):
        _body, status, _ = main.divebar_lookup(MockRequest({"action": "kn_search"}))
        assert status == 400

    def test_dispatch_non_integer_limit_returns_400(self, monkeypatch):
        for bad in ("50", True, 1.5):
            _body, status, _ = main.divebar_lookup(
                MockRequest({"action": "kn_search", "query": "abba", "limit": bad})
            )
            assert status == 400, f"limit={bad!r} should be rejected"
