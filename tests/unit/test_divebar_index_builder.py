"""Tests for divebar mirror index_builder — staging+MERGE approach."""

import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _import_index_builder(monkeypatch):
    """Import index_builder with mocked filename_parser (not available in CI)."""
    # Mock filename_parser before importing index_builder
    mock_fp = MagicMock()
    monkeypatch.setitem(sys.modules, "filename_parser", mock_fp)

    func_dir = str(
        Path(__file__).resolve().parents[2]
        / "infrastructure"
        / "functions"
        / "divebar_mirror"
    )
    monkeypatch.syspath_prepend(func_dir)

    # Import (or reimport) index_builder with mocked deps
    if "index_builder" in sys.modules:
        importlib.reload(sys.modules["index_builder"])
    else:
        importlib.import_module("index_builder")


def _get_mod():
    return sys.modules["index_builder"]


class TestLoadToBigquery:
    """Verify load_to_bigquery uses staging+MERGE instead of WRITE_TRUNCATE on main table."""

    def test_empty_rows_returns_zero(self):
        assert _get_mod().load_to_bigquery("proj", []) == 0

    @patch("index_builder.bigquery.Client")
    def test_loads_to_staging_table_not_main(self, mock_client_cls):
        """The load job must target the staging table, not the main table."""
        mod = _get_mod()
        client = mock_client_cls.return_value
        load_job = MagicMock()
        load_job.output_rows = 5
        client.load_table_from_json.return_value = load_job

        merge_job = MagicMock()
        count_row = MagicMock()
        count_row.cnt = 5
        count_result = MagicMock()
        count_result.result.return_value = [count_row]
        client.query.side_effect = [merge_job, count_result]

        rows = [{"file_id": "abc", "brand": "Test", "filename": "test.mp4"}]
        result = mod.load_to_bigquery("proj", rows)

        # Verify load targets staging table
        load_target = client.load_table_from_json.call_args[0][1]
        assert mod.STAGING_TABLE_ID in load_target
        assert load_target == f"proj.{mod.DATASET_ID}.{mod.STAGING_TABLE_ID}"

        # Verify MERGE SQL references both staging and main tables
        merge_sql = client.query.call_args_list[0][0][0]
        assert "MERGE" in merge_sql
        assert f"{mod.DATASET_ID}.{mod.TABLE_ID}" in merge_sql
        assert f"{mod.DATASET_ID}.{mod.STAGING_TABLE_ID}" in merge_sql

        assert result == 5

    @patch("index_builder.bigquery.Client")
    def test_merge_preserves_gcs_path(self, mock_client_cls):
        """The MERGE SQL must preserve gcs_path on UPDATE and set NULL on INSERT."""
        mod = _get_mod()
        client = mock_client_cls.return_value
        load_job = MagicMock()
        load_job.output_rows = 1
        client.load_table_from_json.return_value = load_job

        merge_job = MagicMock()
        count_row = MagicMock()
        count_row.cnt = 1
        count_result = MagicMock()
        count_result.result.return_value = [count_row]
        client.query.side_effect = [merge_job, count_result]

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

    @patch("index_builder.bigquery.Client")
    def test_merge_deletes_removed_files(self, mock_client_cls):
        """Files no longer in Drive should be deleted from the main table."""
        mod = _get_mod()
        client = mock_client_cls.return_value
        load_job = MagicMock()
        load_job.output_rows = 1
        client.load_table_from_json.return_value = load_job

        merge_job = MagicMock()
        count_row = MagicMock()
        count_row.cnt = 1
        count_result = MagicMock()
        count_result.result.return_value = [count_row]
        client.query.side_effect = [merge_job, count_result]

        mod.load_to_bigquery("proj", [{"file_id": "x"}])

        merge_sql = client.query.call_args_list[0][0][0]
        assert "WHEN NOT MATCHED BY SOURCE THEN DELETE" in merge_sql

    @patch("index_builder.bigquery.Client")
    def test_no_write_truncate_on_main_table(self, mock_client_cls):
        """WRITE_TRUNCATE must only target the staging table, never the main table."""
        mod = _get_mod()
        client = mock_client_cls.return_value
        load_job = MagicMock()
        load_job.output_rows = 1
        client.load_table_from_json.return_value = load_job

        merge_job = MagicMock()
        count_row = MagicMock()
        count_row.cnt = 1
        count_result = MagicMock()
        count_result.result.return_value = [count_row]
        client.query.side_effect = [merge_job, count_result]

        mod.load_to_bigquery("proj", [{"file_id": "x"}])

        # The only load_table_from_json call must target staging, not main
        load_calls = client.load_table_from_json.call_args_list
        assert len(load_calls) == 1
        target = load_calls[0][0][1]
        assert mod.STAGING_TABLE_ID in target
        assert target != f"proj.{mod.DATASET_ID}.{mod.TABLE_ID}"
