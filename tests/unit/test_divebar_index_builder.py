"""Tests for divebar mirror index_builder — staging+MERGE approach."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# Add the Cloud Function source to the path so we can import index_builder
sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[2] / "infrastructure" / "functions" / "divebar_mirror"),
)

from index_builder import load_to_bigquery, STAGING_TABLE_ID, TABLE_ID, DATASET_ID


class TestLoadToBigquery:
    """Verify load_to_bigquery uses staging+MERGE instead of WRITE_TRUNCATE on main table."""

    def test_empty_rows_returns_zero(self):
        assert load_to_bigquery("proj", []) == 0

    @patch("index_builder.bigquery.Client")
    def test_loads_to_staging_table_not_main(self, mock_client_cls):
        """The load job must target the staging table, not the main table."""
        client = mock_client_cls.return_value
        load_job = MagicMock()
        load_job.output_rows = 5
        client.load_table_from_json.return_value = load_job

        # Mock the MERGE query
        merge_job = MagicMock()
        count_row = MagicMock()
        count_row.cnt = 5
        count_result = MagicMock()
        count_result.result.return_value = [count_row]
        client.query.side_effect = [merge_job, count_result]

        rows = [{"file_id": "abc", "brand": "Test", "filename": "test.mp4"}]
        result = load_to_bigquery("proj", rows)

        # Verify load targets staging table
        load_target = client.load_table_from_json.call_args[0][1]
        assert STAGING_TABLE_ID in load_target
        assert load_target == f"proj.{DATASET_ID}.{STAGING_TABLE_ID}"

        # Verify MERGE SQL references both staging and main tables
        merge_sql = client.query.call_args_list[0][0][0]
        assert "MERGE" in merge_sql
        assert f"{DATASET_ID}.{TABLE_ID}" in merge_sql
        assert f"{DATASET_ID}.{STAGING_TABLE_ID}" in merge_sql

        assert result == 5

    @patch("index_builder.bigquery.Client")
    def test_merge_preserves_gcs_path(self, mock_client_cls):
        """The MERGE SQL must preserve gcs_path on UPDATE and set NULL on INSERT."""
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

        load_to_bigquery("proj", [{"file_id": "x"}])

        merge_sql = client.query.call_args_list[0][0][0]

        # gcs_path must NOT appear in the UPDATE SET clause
        # (it should only be in the INSERT clause)
        update_section = merge_sql.split("WHEN MATCHED THEN UPDATE SET")[1].split("WHEN NOT MATCHED")[0]
        assert "gcs_path" not in update_section

        # gcs_path must appear in INSERT with NULL default
        insert_section = merge_sql.split("WHEN NOT MATCHED BY TARGET THEN INSERT")[1].split("WHEN NOT MATCHED BY SOURCE")[0]
        assert "gcs_path" in insert_section
        assert "NULL" in insert_section

    @patch("index_builder.bigquery.Client")
    def test_merge_deletes_removed_files(self, mock_client_cls):
        """Files no longer in Drive should be deleted from the main table."""
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

        load_to_bigquery("proj", [{"file_id": "x"}])

        merge_sql = client.query.call_args_list[0][0][0]
        assert "WHEN NOT MATCHED BY SOURCE THEN DELETE" in merge_sql

    @patch("index_builder.bigquery.Client")
    def test_no_write_truncate_on_main_table(self, mock_client_cls):
        """WRITE_TRUNCATE must only target the staging table, never the main table."""
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

        load_to_bigquery("proj", [{"file_id": "x"}])

        # The only load_table_from_json call must target staging, not main
        load_calls = client.load_table_from_json.call_args_list
        assert len(load_calls) == 1
        target = load_calls[0][0][1]
        assert STAGING_TABLE_ID in target
        assert target != f"proj.{DATASET_ID}.{TABLE_ID}"
