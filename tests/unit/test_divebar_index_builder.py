"""Tests for divebar mirror index_builder — staging+MERGE approach.

The Cloud Function has its own deps (google-cloud-bigquery, filename_parser)
that aren't in the main project's poetry env. We mock them at the module level
before importing index_builder.
"""

import importlib
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Pre-import mocking: google.cloud.bigquery and filename_parser are not
# available in the CI test environment (they're Cloud Function deps).
# ---------------------------------------------------------------------------

_mock_bigquery = MagicMock()
# SchemaField needs to be callable and return a mock
_mock_bigquery.SchemaField = MagicMock

_mock_google = types.ModuleType("google")
_mock_google.cloud = types.ModuleType("google.cloud")
_mock_google.cloud.bigquery = _mock_bigquery

_mock_filename_parser = MagicMock()


@pytest.fixture(autouse=True)
def _import_index_builder(monkeypatch):
    """Import index_builder with mocked external deps."""
    monkeypatch.setitem(sys.modules, "google", _mock_google)
    monkeypatch.setitem(sys.modules, "google.cloud", _mock_google.cloud)
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", _mock_bigquery)
    monkeypatch.setitem(sys.modules, "filename_parser", _mock_filename_parser)

    func_dir = str(
        Path(__file__).resolve().parents[2]
        / "infrastructure"
        / "functions"
        / "divebar_mirror"
    )
    monkeypatch.syspath_prepend(func_dir)

    if "index_builder" in sys.modules:
        importlib.reload(sys.modules["index_builder"])
    else:
        importlib.import_module("index_builder")


def _mod():
    return sys.modules["index_builder"]


class TestLoadToBigquery:
    """Verify load_to_bigquery uses staging+MERGE instead of WRITE_TRUNCATE on main table."""

    def _setup_client_mock(self):
        """Set up a mock BigQuery client with staging load + merge + count."""
        client = MagicMock()
        load_job = MagicMock()
        load_job.output_rows = 1
        client.load_table_from_json.return_value = load_job

        merge_job = MagicMock()
        count_row = MagicMock()
        count_row.cnt = 1
        count_result = MagicMock()
        count_result.result.return_value = [count_row]
        client.query.side_effect = [merge_job, count_result]
        return client

    def test_empty_rows_returns_zero(self):
        assert _mod().load_to_bigquery("proj", []) == 0

    def test_loads_to_staging_table_not_main(self):
        """The load job must target the staging table, not the main table."""
        mod = _mod()
        client = self._setup_client_mock()

        with patch.object(mod.bigquery, "Client", return_value=client):
            mod.load_to_bigquery("proj", [{"file_id": "abc"}])

        load_target = client.load_table_from_json.call_args[0][1]
        assert mod.STAGING_TABLE_ID in load_target
        assert load_target == f"proj.{mod.DATASET_ID}.{mod.STAGING_TABLE_ID}"

        merge_sql = client.query.call_args_list[0][0][0]
        assert "MERGE" in merge_sql
        assert f"{mod.DATASET_ID}.{mod.TABLE_ID}" in merge_sql
        assert f"{mod.DATASET_ID}.{mod.STAGING_TABLE_ID}" in merge_sql

    def test_merge_preserves_gcs_path(self):
        """The MERGE SQL must preserve gcs_path on UPDATE and set NULL on INSERT."""
        mod = _mod()
        client = self._setup_client_mock()

        with patch.object(mod.bigquery, "Client", return_value=client):
            mod.load_to_bigquery("proj", [{"file_id": "x"}])

        merge_sql = client.query.call_args_list[0][0][0]

        # gcs_path must NOT appear in the UPDATE SET clause
        update_section = merge_sql.split("WHEN MATCHED THEN UPDATE SET")[1].split(
            "WHEN NOT MATCHED"
        )[0]
        assert "gcs_path" not in update_section

        # gcs_path must appear in INSERT with NULL default
        insert_section = merge_sql.split("WHEN NOT MATCHED BY TARGET THEN INSERT")[
            1
        ].split("WHEN NOT MATCHED BY SOURCE")[0]
        assert "gcs_path" in insert_section
        assert "NULL" in insert_section

    def test_merge_deletes_removed_files(self):
        """Files no longer in Drive should be deleted from the main table."""
        mod = _mod()
        client = self._setup_client_mock()

        with patch.object(mod.bigquery, "Client", return_value=client):
            mod.load_to_bigquery("proj", [{"file_id": "x"}])

        merge_sql = client.query.call_args_list[0][0][0]
        assert "WHEN NOT MATCHED BY SOURCE THEN DELETE" in merge_sql

    def test_no_write_truncate_on_main_table(self):
        """WRITE_TRUNCATE must only target the staging table, never the main table."""
        mod = _mod()
        client = self._setup_client_mock()

        with patch.object(mod.bigquery, "Client", return_value=client):
            mod.load_to_bigquery("proj", [{"file_id": "x"}])

        load_calls = client.load_table_from_json.call_args_list
        assert len(load_calls) == 1
        target = load_calls[0][0][1]
        assert mod.STAGING_TABLE_ID in target
        assert target != f"proj.{mod.DATASET_ID}.{mod.TABLE_ID}"
