"""Tests for error_monitor.firestore_adapter module.

All tests use a mock Firestore client — no real GCP connections made.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------


def _make_doc_snapshot(exists: bool, data: dict | None = None) -> MagicMock:
    """Return a mock that mimics a Firestore DocumentSnapshot."""
    snap = MagicMock()
    snap.exists = exists
    snap.to_dict.return_value = data or {}
    return snap


def _make_db():
    """Return a mock Firestore client with collection/document chaining."""
    db = MagicMock()
    return db


# ---------------------------------------------------------------------------
# Import helpers (deferred so import errors are clear in tests)
# ---------------------------------------------------------------------------


def _import_adapter():
    from backend.services.error_monitor.firestore_adapter import ErrorPatternsAdapter

    return ErrorPatternsAdapter


def _import_pattern_data():
    from backend.services.error_monitor.firestore_adapter import PatternData

    return PatternData


def _import_upsert_result():
    from backend.services.error_monitor.firestore_adapter import UpsertResult

    return UpsertResult


# ---------------------------------------------------------------------------
# Tests: get_pattern
# ---------------------------------------------------------------------------


class TestGetPattern:
    """get_pattern should return None for missing docs and dict for existing."""

    def test_returns_none_for_missing_doc(self):
        db = _make_db()
        snap = _make_doc_snapshot(exists=False)
        db.collection.return_value.document.return_value.get.return_value = snap

        Adapter = _import_adapter()
        adapter = Adapter(db=db)
        result = adapter.get_pattern("pattern_abc123")

        assert result is None

    def test_returns_dict_for_existing_doc(self):
        db = _make_db()
        expected = {
            "pattern_id": "pattern_abc123",
            "service": "karaoke-backend",
            "status": "new",
        }
        snap = _make_doc_snapshot(exists=True, data=expected)
        db.collection.return_value.document.return_value.get.return_value = snap

        Adapter = _import_adapter()
        adapter = Adapter(db=db)
        result = adapter.get_pattern("pattern_abc123")

        assert result == expected

    def test_queries_correct_collection_and_doc(self):
        db = _make_db()
        snap = _make_doc_snapshot(exists=False)
        doc_ref = MagicMock()
        doc_ref.get.return_value = snap
        col_ref = MagicMock()
        col_ref.document.return_value = doc_ref
        db.collection.return_value = col_ref

        Adapter = _import_adapter()
        adapter = Adapter(db=db)
        adapter.get_pattern("my_pattern_id")

        db.collection.assert_called_with("error_patterns")
        col_ref.document.assert_called_with("my_pattern_id")


# ---------------------------------------------------------------------------
# Tests: upsert_pattern — new pattern
# ---------------------------------------------------------------------------


class TestUpsertPatternNew:
    """upsert_pattern should create new patterns with correct initial fields."""

    def test_creates_new_pattern_when_not_exists(self):
        db = _make_db()
        # get() returns missing doc — triggers create path
        snap = _make_doc_snapshot(exists=False)
        doc_ref = MagicMock()
        doc_ref.get.return_value = snap
        db.collection.return_value.document.return_value = doc_ref

        PatternData = _import_pattern_data()
        Adapter = _import_adapter()
        adapter = Adapter(db=db)

        now = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
        data = PatternData(
            pattern_id="pat_123",
            service="karaoke-backend",
            resource_type="cloud_run_service",
            normalized_message="Connection refused",
            sample_message="Connection refused to 10.0.0.1:5432",
            count=3,
            timestamp=now,
        )
        result = adapter.upsert_pattern(data)

        # The doc_ref.set() should have been called (new pattern)
        doc_ref.set.assert_called_once()
        created_doc = doc_ref.set.call_args[0][0]

        assert created_doc["pattern_id"] == "pat_123"
        assert created_doc["service"] == "karaoke-backend"
        assert created_doc["status"] == "new"
        assert created_doc["severity"] == "P3"
        assert created_doc["total_count"] == 3
        assert created_doc["sample_message"] == "Connection refused to 10.0.0.1:5432"

    def test_new_pattern_result_is_new(self):
        db = _make_db()
        snap = _make_doc_snapshot(exists=False)
        doc_ref = MagicMock()
        doc_ref.get.return_value = snap
        db.collection.return_value.document.return_value = doc_ref

        PatternData = _import_pattern_data()
        Adapter = _import_adapter()
        adapter = Adapter(db=db)

        now = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
        data = PatternData(
            pattern_id="pat_123",
            service="karaoke-backend",
            resource_type="cloud_run_service",
            normalized_message="Connection refused",
            sample_message="Connection refused",
            count=1,
            timestamp=now,
        )
        result = adapter.upsert_pattern(data)

        UpsertResult = _import_upsert_result()
        assert isinstance(result, UpsertResult)
        assert result.is_new is True
        assert result.pattern_id == "pat_123"
        assert result.previous_status is None

    def test_new_pattern_has_rolling_counts(self):
        db = _make_db()
        snap = _make_doc_snapshot(exists=False)
        doc_ref = MagicMock()
        doc_ref.get.return_value = snap
        db.collection.return_value.document.return_value = doc_ref

        PatternData = _import_pattern_data()
        Adapter = _import_adapter()
        adapter = Adapter(db=db)

        now = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
        data = PatternData(
            pattern_id="pat_123",
            service="karaoke-backend",
            resource_type="cloud_run_service",
            normalized_message="Connection refused",
            sample_message="Connection refused",
            count=5,
            timestamp=now,
        )
        adapter.upsert_pattern(data)
        created_doc = doc_ref.set.call_args[0][0]

        # rolling_counts should be a list with one entry
        assert "rolling_counts" in created_doc
        assert isinstance(created_doc["rolling_counts"], list)
        assert len(created_doc["rolling_counts"]) == 1
        assert created_doc["rolling_counts"][0]["count"] == 5


# ---------------------------------------------------------------------------
# Tests: upsert_pattern — existing pattern
# ---------------------------------------------------------------------------


class TestUpsertPatternExisting:
    """upsert_pattern should update existing patterns and increment counts."""

    def _make_existing_pattern(self, status: str = "new") -> dict:
        now = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
        return {
            "pattern_id": "pat_existing",
            "service": "karaoke-backend",
            "resource_type": "cloud_run_service",
            "normalized_message": "Connection refused",
            "sample_message": "old sample",
            "status": status,
            "severity": "P3",
            "total_count": 10,
            "rolling_counts": [
                {"ts": (now - timedelta(hours=2)).isoformat(), "count": 10}
            ],
            "first_seen": now.isoformat(),
            "last_seen": (now - timedelta(hours=2)).isoformat(),
            "alerted_at": None,
        }

    def test_increments_total_count(self):
        db = _make_db()
        existing = self._make_existing_pattern()
        snap = _make_doc_snapshot(exists=True, data=existing)
        doc_ref = MagicMock()
        doc_ref.get.return_value = snap
        db.collection.return_value.document.return_value = doc_ref

        PatternData = _import_pattern_data()
        Adapter = _import_adapter()
        adapter = Adapter(db=db)

        now = datetime(2026, 4, 10, 14, 0, 0, tzinfo=timezone.utc)
        data = PatternData(
            pattern_id="pat_existing",
            service="karaoke-backend",
            resource_type="cloud_run_service",
            normalized_message="Connection refused",
            sample_message="new sample",
            count=3,
            timestamp=now,
        )
        adapter.upsert_pattern(data)

        # update() should have been called
        doc_ref.update.assert_called_once()
        updated = doc_ref.update.call_args[0][0]
        assert updated["total_count"] == 13  # 10 + 3

    def test_existing_pattern_result_is_not_new(self):
        db = _make_db()
        existing = self._make_existing_pattern(status="new")
        snap = _make_doc_snapshot(exists=True, data=existing)
        doc_ref = MagicMock()
        doc_ref.get.return_value = snap
        db.collection.return_value.document.return_value = doc_ref

        PatternData = _import_pattern_data()
        Adapter = _import_adapter()
        adapter = Adapter(db=db)

        now = datetime(2026, 4, 10, 14, 0, 0, tzinfo=timezone.utc)
        data = PatternData(
            pattern_id="pat_existing",
            service="karaoke-backend",
            resource_type="cloud_run_service",
            normalized_message="Connection refused",
            sample_message="new sample",
            count=3,
            timestamp=now,
        )
        result = adapter.upsert_pattern(data)

        assert result.is_new is False
        assert result.previous_status == "new"

    def test_updates_sample_message(self):
        db = _make_db()
        existing = self._make_existing_pattern()
        snap = _make_doc_snapshot(exists=True, data=existing)
        doc_ref = MagicMock()
        doc_ref.get.return_value = snap
        db.collection.return_value.document.return_value = doc_ref

        PatternData = _import_pattern_data()
        Adapter = _import_adapter()
        adapter = Adapter(db=db)

        now = datetime(2026, 4, 10, 14, 0, 0, tzinfo=timezone.utc)
        data = PatternData(
            pattern_id="pat_existing",
            service="karaoke-backend",
            resource_type="cloud_run_service",
            normalized_message="Connection refused",
            sample_message="updated sample message",
            count=1,
            timestamp=now,
        )
        adapter.upsert_pattern(data)

        updated = doc_ref.update.call_args[0][0]
        assert updated["sample_message"] == "updated sample message"

    def test_updates_rolling_counts(self):
        db = _make_db()
        existing = self._make_existing_pattern()
        snap = _make_doc_snapshot(exists=True, data=existing)
        doc_ref = MagicMock()
        doc_ref.get.return_value = snap
        db.collection.return_value.document.return_value = doc_ref

        PatternData = _import_pattern_data()
        Adapter = _import_adapter()
        adapter = Adapter(db=db)

        now = datetime(2026, 4, 10, 14, 0, 0, tzinfo=timezone.utc)
        data = PatternData(
            pattern_id="pat_existing",
            service="karaoke-backend",
            resource_type="cloud_run_service",
            normalized_message="Connection refused",
            sample_message="new sample",
            count=5,
            timestamp=now,
        )
        # Freeze _utcnow so prune_rolling_counts keeps the existing entry
        # (2h-old relative to fixture now, not relative to real clock time).
        with patch("backend.services.error_monitor.firestore_adapter._utcnow", return_value=now):
            adapter.upsert_pattern(data)

        updated = doc_ref.update.call_args[0][0]
        assert "rolling_counts" in updated
        # Should have 2 entries now: original + new
        assert len(updated["rolling_counts"]) == 2
        assert updated["rolling_counts"][-1]["count"] == 5


# ---------------------------------------------------------------------------
# Tests: upsert_pattern — reactivation
# ---------------------------------------------------------------------------


class TestUpsertPatternReactivation:
    """upsert_pattern should reactivate auto_resolved and fixed patterns."""

    def _make_resolved_pattern(self, status: str) -> dict:
        now = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
        return {
            "pattern_id": "pat_resolved",
            "service": "karaoke-backend",
            "resource_type": "cloud_run_service",
            "normalized_message": "Connection refused",
            "sample_message": "old sample",
            "status": status,
            "severity": "P3",
            "total_count": 10,
            "rolling_counts": [],
            "first_seen": now.isoformat(),
            "last_seen": (now - timedelta(days=3)).isoformat(),
            "alerted_at": None,
        }

    def test_reactivates_auto_resolved_pattern(self):
        db = _make_db()
        existing = self._make_resolved_pattern(status="auto_resolved")
        snap = _make_doc_snapshot(exists=True, data=existing)
        doc_ref = MagicMock()
        doc_ref.get.return_value = snap
        db.collection.return_value.document.return_value = doc_ref

        PatternData = _import_pattern_data()
        Adapter = _import_adapter()
        adapter = Adapter(db=db)

        now = datetime(2026, 4, 10, 15, 0, 0, tzinfo=timezone.utc)
        data = PatternData(
            pattern_id="pat_resolved",
            service="karaoke-backend",
            resource_type="cloud_run_service",
            normalized_message="Connection refused",
            sample_message="new occurrence",
            count=2,
            timestamp=now,
        )
        result = adapter.upsert_pattern(data)

        # is_new should be True on reactivation
        assert result.is_new is True
        assert result.previous_status == "auto_resolved"

        updated = doc_ref.update.call_args[0][0]
        assert updated["status"] == "new"

    def test_reactivates_fixed_pattern(self):
        db = _make_db()
        existing = self._make_resolved_pattern(status="fixed")
        snap = _make_doc_snapshot(exists=True, data=existing)
        doc_ref = MagicMock()
        doc_ref.get.return_value = snap
        db.collection.return_value.document.return_value = doc_ref

        PatternData = _import_pattern_data()
        Adapter = _import_adapter()
        adapter = Adapter(db=db)

        now = datetime(2026, 4, 10, 15, 0, 0, tzinfo=timezone.utc)
        data = PatternData(
            pattern_id="pat_resolved",
            service="karaoke-backend",
            resource_type="cloud_run_service",
            normalized_message="Connection refused",
            sample_message="regression!",
            count=1,
            timestamp=now,
        )
        result = adapter.upsert_pattern(data)

        assert result.is_new is True
        assert result.previous_status == "fixed"
        updated = doc_ref.update.call_args[0][0]
        assert updated["status"] == "new"

    def test_acknowledged_pattern_not_reactivated(self):
        """Acknowledged patterns should NOT be flipped back to 'new'."""
        db = _make_db()
        existing = self._make_resolved_pattern(status="acknowledged")
        snap = _make_doc_snapshot(exists=True, data=existing)
        doc_ref = MagicMock()
        doc_ref.get.return_value = snap
        db.collection.return_value.document.return_value = doc_ref

        PatternData = _import_pattern_data()
        Adapter = _import_adapter()
        adapter = Adapter(db=db)

        now = datetime(2026, 4, 10, 15, 0, 0, tzinfo=timezone.utc)
        data = PatternData(
            pattern_id="pat_resolved",
            service="karaoke-backend",
            resource_type="cloud_run_service",
            normalized_message="Connection refused",
            sample_message="still happening",
            count=2,
            timestamp=now,
        )
        result = adapter.upsert_pattern(data)

        assert result.is_new is False
        updated = doc_ref.update.call_args[0][0]
        # Status should NOT be reset to "new"
        assert updated.get("status") != "new"


# ---------------------------------------------------------------------------
# Tests: get_active_patterns
# ---------------------------------------------------------------------------


class TestGetActivePatterns:
    """get_active_patterns should return patterns with status in [new, acknowledged, known]."""

    def test_returns_active_pattern_dicts(self):
        db = _make_db()
        pattern_1 = {"pattern_id": "p1", "status": "new"}
        pattern_2 = {"pattern_id": "p2", "status": "acknowledged"}
        pattern_3 = {"pattern_id": "p3", "status": "known"}

        snap_1 = _make_doc_snapshot(exists=True, data=pattern_1)
        snap_2 = _make_doc_snapshot(exists=True, data=pattern_2)
        snap_3 = _make_doc_snapshot(exists=True, data=pattern_3)

        # Query chain: collection.where.stream
        query_mock = MagicMock()
        query_mock.stream.return_value = [snap_1, snap_2, snap_3]
        db.collection.return_value.where.return_value = query_mock

        Adapter = _import_adapter()
        adapter = Adapter(db=db)
        results = adapter.get_active_patterns()

        assert len(results) == 3
        assert results[0]["pattern_id"] == "p1"
        assert results[1]["status"] == "acknowledged"

    def test_queries_correct_collection(self):
        db = _make_db()
        query_mock = MagicMock()
        query_mock.stream.return_value = []
        db.collection.return_value.where.return_value = query_mock

        Adapter = _import_adapter()
        adapter = Adapter(db=db)
        adapter.get_active_patterns()

        db.collection.assert_called_with("error_patterns")


# ---------------------------------------------------------------------------
# Tests: _compute_resolve_threshold
# ---------------------------------------------------------------------------


class TestComputeResolveThreshold:
    """_compute_resolve_threshold should return frequency-aware threshold."""

    def test_fewer_than_3_points_returns_fallback(self):
        Adapter = _import_adapter()
        adapter = Adapter(db=_make_db())

        # 0 data points
        result = adapter._compute_resolve_threshold([])
        assert result == 48  # AUTO_RESOLVE_FALLBACK_HOURS

    def test_one_point_returns_fallback(self):
        Adapter = _import_adapter()
        adapter = Adapter(db=_make_db())

        now = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
        rolling = [{"ts": now.isoformat(), "count": 5}]
        result = adapter._compute_resolve_threshold(rolling)
        assert result == 48

    def test_two_points_returns_fallback(self):
        Adapter = _import_adapter()
        adapter = Adapter(db=_make_db())

        now = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
        rolling = [
            {"ts": (now - timedelta(hours=4)).isoformat(), "count": 3},
            {"ts": now.isoformat(), "count": 2},
        ]
        result = adapter._compute_resolve_threshold(rolling)
        assert result == 48

    def test_sufficient_data_returns_clamped_value(self):
        """With 3+ data points, result should be in [6, 168] range."""
        Adapter = _import_adapter()
        adapter = Adapter(db=_make_db())

        # 3 points separated by 1 hour each → mean_interval = 1h
        # threshold = 1 * 8 = 8 → clamped to [6, 168] → 8
        now = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
        rolling = [
            {"ts": (now - timedelta(hours=2)).isoformat(), "count": 5},
            {"ts": (now - timedelta(hours=1)).isoformat(), "count": 5},
            {"ts": now.isoformat(), "count": 5},
        ]
        result = adapter._compute_resolve_threshold(rolling)

        assert 6 <= result <= 168

    def test_very_frequent_errors_clamped_to_min(self):
        """Very frequent (sub-minute) errors → threshold clamped to AUTO_RESOLVE_MIN_HOURS."""
        Adapter = _import_adapter()
        adapter = Adapter(db=_make_db())

        # 3 points separated by 1 minute each → mean_interval = 0.0167h
        # threshold = 0.0167 * 8 = 0.133 → clamped to 6
        now = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
        rolling = [
            {"ts": (now - timedelta(minutes=2)).isoformat(), "count": 100},
            {"ts": (now - timedelta(minutes=1)).isoformat(), "count": 100},
            {"ts": now.isoformat(), "count": 100},
        ]
        result = adapter._compute_resolve_threshold(rolling)
        assert result == 6  # AUTO_RESOLVE_MIN_HOURS

    def test_very_infrequent_errors_clamped_to_max(self):
        """Very infrequent errors → threshold clamped to AUTO_RESOLVE_MAX_HOURS."""
        Adapter = _import_adapter()
        adapter = Adapter(db=_make_db())

        # 3 points separated by 30 days each → mean_interval = 720h
        # threshold = 720 * 8 → clamped to 168
        now = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
        rolling = [
            {"ts": (now - timedelta(days=60)).isoformat(), "count": 1},
            {"ts": (now - timedelta(days=30)).isoformat(), "count": 1},
            {"ts": now.isoformat(), "count": 1},
        ]
        result = adapter._compute_resolve_threshold(rolling)
        assert result == 168  # AUTO_RESOLVE_MAX_HOURS


# ---------------------------------------------------------------------------
# Tests: create_incident
# ---------------------------------------------------------------------------


class TestCreateIncident:
    """create_incident should write an incident doc and link patterns."""

    def test_creates_incident_doc(self):
        db = _make_db()
        incident_ref = MagicMock()
        db.collection.return_value.document.return_value = incident_ref

        Adapter = _import_adapter()
        adapter = Adapter(db=db)

        incident_id = adapter.create_incident(
            title="Database connection failures",
            root_cause="Postgres is down",
            severity="P1",
            suggested_fix="Restart DB",
            primary_service="karaoke-backend",
            pattern_ids=["pat_1", "pat_2"],
            used_llm=True,
        )

        # incident_ref.set() should have been called
        incident_ref.set.assert_called_once()
        doc_data = incident_ref.set.call_args[0][0]

        assert doc_data["title"] == "Database connection failures"
        assert doc_data["root_cause"] == "Postgres is down"
        assert doc_data["severity"] == "P1"
        assert doc_data["suggested_fix"] == "Restart DB"
        assert doc_data["primary_service"] == "karaoke-backend"
        assert doc_data["pattern_ids"] == ["pat_1", "pat_2"]
        assert doc_data["used_llm"] is True

    def test_incident_id_format(self):
        """Incident ID should match inc_YYYYMMDD_HHMM_<8hex> format."""
        import re

        db = _make_db()
        doc_ref = MagicMock()
        db.collection.return_value.document.return_value = doc_ref

        Adapter = _import_adapter()
        adapter = Adapter(db=db)

        incident_id = adapter.create_incident(
            title="Test incident",
            root_cause="Unknown",
            severity="P3",
            suggested_fix="Wait",
            primary_service="karaoke-backend",
            pattern_ids=[],
            used_llm=False,
        )

        assert re.match(r"^inc_\d{8}_\d{4}_[0-9a-f]{8}$", incident_id), (
            f"Unexpected incident_id format: {incident_id}"
        )

    def test_links_patterns_via_update(self):
        """Each pattern in pattern_ids should be linked to the incident."""
        db = _make_db()

        incident_ref = MagicMock()
        # We need to differentiate between collection calls for incidents vs patterns
        pattern_ref = MagicMock()

        call_count = [0]

        def collection_side_effect(name):
            mock = MagicMock()
            if name == "error_incidents":
                mock.document.return_value = incident_ref
            elif name == "error_patterns":
                mock.document.return_value = pattern_ref
            return mock

        db.collection.side_effect = collection_side_effect

        Adapter = _import_adapter()
        adapter = Adapter(db=db)

        adapter.create_incident(
            title="Test",
            root_cause="Unknown",
            severity="P3",
            suggested_fix="Fix it",
            primary_service="svc",
            pattern_ids=["pat_1", "pat_2"],
            used_llm=False,
        )

        # pattern_ref.update should have been called twice (once per pattern)
        assert pattern_ref.update.call_count == 2


# ---------------------------------------------------------------------------
# Tests: log_discord_alert
# ---------------------------------------------------------------------------


class TestLogDiscordAlert:
    """log_discord_alert should write an audit doc to discord_alerts."""

    def test_writes_alert_doc(self):
        db = _make_db()
        alert_ref = MagicMock()
        db.collection.return_value.document.return_value = alert_ref

        Adapter = _import_adapter()
        adapter = Adapter(db=db)

        adapter.log_discord_alert(
            alert_type="new_pattern",
            content="Error in karaoke-backend",
            success=True,
            metadata={"pattern_id": "pat_123"},
        )

        alert_ref.set.assert_called_once()
        doc_data = alert_ref.set.call_args[0][0]

        assert doc_data["alert_type"] == "new_pattern"
        assert doc_data["content"] == "Error in karaoke-backend"
        assert doc_data["success"] is True
        assert doc_data["metadata"]["pattern_id"] == "pat_123"

    def test_alert_id_format(self):
        """Alert ID should match alert_YYYYMMDD_HHMMSS_<8hex> format."""
        import re

        db = _make_db()
        doc_ref = MagicMock()
        col_ref = MagicMock()
        col_ref.document.return_value = doc_ref
        db.collection.return_value = col_ref

        Adapter = _import_adapter()
        adapter = Adapter(db=db)

        # Capture the document ID used
        adapter.log_discord_alert(
            alert_type="digest",
            content="All clear",
            success=True,
        )

        called_with = col_ref.document.call_args[0][0]
        assert re.match(r"^alert_\d{8}_\d{6}_[0-9a-f]{8}$", called_with), (
            f"Unexpected alert_id format: {called_with}"
        )

    def test_metadata_defaults_to_empty_dict(self):
        db = _make_db()
        alert_ref = MagicMock()
        db.collection.return_value.document.return_value = alert_ref

        Adapter = _import_adapter()
        adapter = Adapter(db=db)

        adapter.log_discord_alert(
            alert_type="test",
            content="hello",
            success=False,
        )

        doc_data = alert_ref.set.call_args[0][0]
        assert doc_data["metadata"] == {}


# ---------------------------------------------------------------------------
# Tests: merge_pattern
# ---------------------------------------------------------------------------


class TestMergePattern:
    """merge_pattern should set source status to 'muted' and record merged_into."""

    def test_sets_muted_status(self):
        db = _make_db()
        source_ref = MagicMock()
        db.collection.return_value.document.return_value = source_ref

        Adapter = _import_adapter()
        adapter = Adapter(db=db)

        adapter.merge_pattern(
            source_id="pat_source",
            target_id="pat_target",
            reason="Duplicate error patterns",
        )

        source_ref.update.assert_called_once()
        updated = source_ref.update.call_args[0][0]
        assert updated["status"] == "muted"

    def test_records_merged_into(self):
        db = _make_db()
        source_ref = MagicMock()
        db.collection.return_value.document.return_value = source_ref

        Adapter = _import_adapter()
        adapter = Adapter(db=db)

        adapter.merge_pattern(
            source_id="pat_source",
            target_id="pat_target",
            reason="Duplicate",
        )

        updated = source_ref.update.call_args[0][0]
        assert updated["merged_into"] == "pat_target"
        assert updated["merge_reason"] == "Duplicate"


# ---------------------------------------------------------------------------
# Tests: update_pattern_alerted
# ---------------------------------------------------------------------------


class TestUpdatePatternAlerted:
    """update_pattern_alerted should set alerted_at timestamp."""

    def test_sets_alerted_at(self):
        db = _make_db()
        pattern_ref = MagicMock()
        db.collection.return_value.document.return_value = pattern_ref

        Adapter = _import_adapter()
        adapter = Adapter(db=db)

        adapter.update_pattern_alerted("pat_123")

        pattern_ref.update.assert_called_once()
        updated = pattern_ref.update.call_args[0][0]
        assert "alerted_at" in updated
        assert updated["alerted_at"] is not None

    def test_queries_correct_pattern(self):
        db = _make_db()
        col_ref = MagicMock()
        pattern_ref = MagicMock()
        col_ref.document.return_value = pattern_ref
        db.collection.return_value = col_ref

        Adapter = _import_adapter()
        adapter = Adapter(db=db)

        adapter.update_pattern_alerted("my_pattern_id")

        db.collection.assert_called_with("error_patterns")
        col_ref.document.assert_called_with("my_pattern_id")


# ---------------------------------------------------------------------------
# Tests: auto_resolve_pattern
# ---------------------------------------------------------------------------


class TestAutoResolvePattern:
    """auto_resolve_pattern should set status to 'auto_resolved'."""

    def test_sets_auto_resolved_status(self):
        db = _make_db()
        pattern_ref = MagicMock()
        db.collection.return_value.document.return_value = pattern_ref

        Adapter = _import_adapter()
        adapter = Adapter(db=db)

        adapter.auto_resolve_pattern("pat_123", hours_silent=24.5)

        pattern_ref.update.assert_called_once()
        updated = pattern_ref.update.call_args[0][0]
        assert updated["status"] == "auto_resolved"

    def test_records_hours_silent(self):
        db = _make_db()
        pattern_ref = MagicMock()
        db.collection.return_value.document.return_value = pattern_ref

        Adapter = _import_adapter()
        adapter = Adapter(db=db)

        adapter.auto_resolve_pattern("pat_123", hours_silent=36.0)

        updated = pattern_ref.update.call_args[0][0]
        assert updated["auto_resolve_hours_silent"] == 36.0


# ---------------------------------------------------------------------------
# Tests: resolve_pattern
# ---------------------------------------------------------------------------


class TestResolvePattern:
    """resolve_pattern should set status to 'fixed' with fixed_by map."""

    def test_sets_fixed_status(self):
        db = _make_db()
        pattern_ref = MagicMock()
        db.collection.return_value.document.return_value = pattern_ref

        Adapter = _import_adapter()
        adapter = Adapter(db=db)

        adapter.resolve_pattern("pat_123", pr_url="https://github.com/pr/1", note="Fixed DB")

        pattern_ref.update.assert_called_once()
        updated = pattern_ref.update.call_args[0][0]
        assert updated["status"] == "fixed"

    def test_includes_fixed_by_map(self):
        db = _make_db()
        pattern_ref = MagicMock()
        db.collection.return_value.document.return_value = pattern_ref

        Adapter = _import_adapter()
        adapter = Adapter(db=db)

        adapter.resolve_pattern("pat_123", pr_url="https://github.com/pr/42", note="Hotfix")

        updated = pattern_ref.update.call_args[0][0]
        assert "fixed_by" in updated
        assert updated["fixed_by"]["pr_url"] == "https://github.com/pr/42"
        assert updated["fixed_by"]["note"] == "Hotfix"

    def test_works_without_pr_url(self):
        db = _make_db()
        pattern_ref = MagicMock()
        db.collection.return_value.document.return_value = pattern_ref

        Adapter = _import_adapter()
        adapter = Adapter(db=db)

        # Should not raise
        adapter.resolve_pattern("pat_123")

        updated = pattern_ref.update.call_args[0][0]
        assert updated["status"] == "fixed"


# ---------------------------------------------------------------------------
# Tests: check_auto_resolve
# ---------------------------------------------------------------------------


class TestCheckAutoResolve:
    """check_auto_resolve should return hours_silent if threshold exceeded, None otherwise."""

    def _make_pattern(self, last_seen_hours_ago: float, status: str = "new") -> dict:
        now = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
        last_seen = now - timedelta(hours=last_seen_hours_ago)
        # Build minimal rolling_counts with 3 data points for non-fallback threshold
        rolling = [
            {"ts": (last_seen - timedelta(hours=2)).isoformat(), "count": 5},
            {"ts": (last_seen - timedelta(hours=1)).isoformat(), "count": 5},
            {"ts": last_seen.isoformat(), "count": 5},
        ]
        return {
            "pattern_id": "pat_test",
            "status": status,
            "last_seen": last_seen.isoformat(),
            "rolling_counts": rolling,
        }

    def test_returns_none_when_recently_seen(self):
        """Pattern seen 1 hour ago should NOT be auto-resolved."""
        Adapter = _import_adapter()
        adapter = Adapter(db=_make_db())

        pattern = self._make_pattern(last_seen_hours_ago=1.0)
        with patch("backend.services.error_monitor.firestore_adapter.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
            mock_dt.fromisoformat = datetime.fromisoformat
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = adapter.check_auto_resolve(pattern)

        assert result is None

    def test_returns_hours_silent_when_threshold_exceeded(self):
        """Pattern silent for 100h should be returned for auto-resolve."""
        Adapter = _import_adapter()
        adapter = Adapter(db=_make_db())

        # Use fallback threshold (48h) by providing <3 rolling_counts
        now = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
        last_seen = now - timedelta(hours=100)
        pattern = {
            "pattern_id": "pat_old",
            "status": "new",
            "last_seen": last_seen.isoformat(),
            "rolling_counts": [],  # <3 points → fallback = 48h
        }

        with patch("backend.services.error_monitor.firestore_adapter.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.fromisoformat = datetime.fromisoformat
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = adapter.check_auto_resolve(pattern)

        assert result is not None
        assert result > 48.0  # hours_silent should be ~100

    def test_muted_pattern_returns_none(self):
        """Muted patterns should never be auto-resolved."""
        Adapter = _import_adapter()
        adapter = Adapter(db=_make_db())

        now = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
        last_seen = now - timedelta(hours=100)
        pattern = {
            "pattern_id": "pat_muted",
            "status": "muted",
            "last_seen": last_seen.isoformat(),
            "rolling_counts": [],
        }

        with patch("backend.services.error_monitor.firestore_adapter.datetime") as mock_dt:
            mock_dt.now.return_value = now
            mock_dt.fromisoformat = datetime.fromisoformat
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = adapter.check_auto_resolve(pattern)

        assert result is None


# ---------------------------------------------------------------------------
# Tests: _prune_rolling_counts
# ---------------------------------------------------------------------------


class TestPruneRollingCounts:
    """_prune_rolling_counts should remove entries older than 7 days."""

    def test_removes_old_entries(self):
        Adapter = _import_adapter()
        adapter = Adapter(db=_make_db())

        now = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
        rolling = [
            {"ts": (now - timedelta(days=10)).isoformat(), "count": 5},  # old
            {"ts": (now - timedelta(days=8)).isoformat(), "count": 3},   # old
            {"ts": (now - timedelta(days=3)).isoformat(), "count": 7},   # recent
            {"ts": now.isoformat(), "count": 2},                          # recent
        ]

        with patch("backend.services.error_monitor.firestore_adapter._utcnow", return_value=now):
            result = adapter._prune_rolling_counts(rolling)

        assert len(result) == 2
        assert result[0]["count"] == 7
        assert result[1]["count"] == 2

    def test_keeps_all_recent_entries(self):
        Adapter = _import_adapter()
        adapter = Adapter(db=_make_db())

        now = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
        rolling = [
            {"ts": (now - timedelta(days=1)).isoformat(), "count": 1},
            {"ts": (now - timedelta(days=2)).isoformat(), "count": 2},
            {"ts": now.isoformat(), "count": 3},
        ]

        with patch("backend.services.error_monitor.firestore_adapter._utcnow", return_value=now):
            result = adapter._prune_rolling_counts(rolling)
        assert len(result) == 3

    def test_empty_list_returns_empty(self):
        Adapter = _import_adapter()
        adapter = Adapter(db=_make_db())

        result = adapter._prune_rolling_counts([])
        assert result == []


# ---------------------------------------------------------------------------
# Tests: get_patterns_for_auto_resolve
# ---------------------------------------------------------------------------


class TestGetPatternsForAutoResolve:
    """get_patterns_for_auto_resolve should return new and acknowledged patterns."""

    def test_queries_correct_statuses(self):
        db = _make_db()
        query_mock = MagicMock()
        query_mock.stream.return_value = []
        db.collection.return_value.where.return_value = query_mock

        Adapter = _import_adapter()
        adapter = Adapter(db=db)
        adapter.get_patterns_for_auto_resolve()

        # Collection should be error_patterns
        db.collection.assert_called_with("error_patterns")

    def test_returns_list_of_dicts(self):
        db = _make_db()
        snap = _make_doc_snapshot(exists=True, data={"pattern_id": "p1", "status": "new"})
        query_mock = MagicMock()
        query_mock.stream.return_value = [snap]
        db.collection.return_value.where.return_value = query_mock

        Adapter = _import_adapter()
        adapter = Adapter(db=db)
        results = adapter.get_patterns_for_auto_resolve()

        assert isinstance(results, list)
        assert results[0]["pattern_id"] == "p1"


# ---------------------------------------------------------------------------
# Tests: get_unalerted_new_patterns
# ---------------------------------------------------------------------------


class TestGetUnalertedNewPatterns:
    """get_unalerted_new_patterns should filter for status=new AND alerted_at=None."""

    def test_queries_correct_collection_and_filters(self):
        db = _make_db()
        # Two chained .where() calls, final stream() returns []
        second_where = MagicMock()
        second_where.stream.return_value = []
        first_where = MagicMock()
        first_where.where.return_value = second_where
        db.collection.return_value.where.return_value = first_where

        Adapter = _import_adapter()
        adapter = Adapter(db=db)
        adapter.get_unalerted_new_patterns()

        db.collection.assert_called_with("error_patterns")
        # first .where should filter status=="new"
        first_where_call = db.collection.return_value.where.call_args
        assert first_where_call is not None
        # second .where should filter alerted_at==None
        second_where_call = first_where.where.call_args
        assert second_where_call is not None

    def test_returns_list_of_dicts(self):
        db = _make_db()
        snap = _make_doc_snapshot(
            exists=True,
            data={
                "pattern_id": "fe_p1",
                "service": "frontend",
                "status": "new",
                "alerted_at": None,
            },
        )
        second_where = MagicMock()
        second_where.stream.return_value = [snap]
        first_where = MagicMock()
        first_where.where.return_value = second_where
        db.collection.return_value.where.return_value = first_where

        Adapter = _import_adapter()
        adapter = Adapter(db=db)
        results = adapter.get_unalerted_new_patterns()

        assert isinstance(results, list)
        assert len(results) == 1
        assert results[0]["pattern_id"] == "fe_p1"
        assert results[0]["service"] == "frontend"

    def test_returns_empty_when_no_matches(self):
        db = _make_db()
        second_where = MagicMock()
        second_where.stream.return_value = []
        first_where = MagicMock()
        first_where.where.return_value = second_where
        db.collection.return_value.where.return_value = first_where

        Adapter = _import_adapter()
        adapter = Adapter(db=db)
        results = adapter.get_unalerted_new_patterns()

        assert results == []
