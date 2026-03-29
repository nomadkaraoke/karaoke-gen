"""Tests for bigquery_export module."""

import unittest
from unittest.mock import MagicMock, patch

DAILY_REFRESH_TABLES = [
    "karaokenerds_raw",
    "karaokenerds_community",
    "divebar_catalog",
    "kn_divebar_xref",
]

MONTHLY_TABLES = [
    "mb_artists",
    "mb_recordings",
    "mb_artist_tags",
    "mb_recording_isrc",
    "mbid_spotify_mapping",
    "mb_artists_normalized",
    "karaoke_recording_links",
    "mlhd_artist_similarity",
]


class TestBigQueryExport(unittest.TestCase):
    @patch("bigquery_export.bigquery.Client")
    def test_weekly_export_on_sunday(self, mock_client_class):
        from bigquery_export import export_bigquery_tables

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_job = MagicMock()
        mock_job.result.return_value = None
        mock_client.extract_table.return_value = mock_job

        result = export_bigquery_tables(
            project="test",
            staging_bucket="staging",
            date_str="2026-03-29",
            day_of_week=6,
            day_of_month=29,
        )

        assert mock_client.extract_table.call_count == len(DAILY_REFRESH_TABLES)

    @patch("bigquery_export.bigquery.Client")
    def test_monthly_export_on_first(self, mock_client_class):
        from bigquery_export import export_bigquery_tables

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_job = MagicMock()
        mock_job.result.return_value = None
        mock_client.extract_table.return_value = mock_job

        result = export_bigquery_tables(
            project="test",
            staging_bucket="staging",
            date_str="2026-04-01",
            day_of_week=2,
            day_of_month=1,
        )

        assert mock_client.extract_table.call_count == len(MONTHLY_TABLES)

    @patch("bigquery_export.bigquery.Client")
    def test_no_export_on_regular_day(self, mock_client_class):
        from bigquery_export import export_bigquery_tables

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        result = export_bigquery_tables(
            project="test",
            staging_bucket="staging",
            date_str="2026-03-25",
            day_of_week=1,
            day_of_month=25,
        )

        mock_client.extract_table.assert_not_called()
        assert "skipped" in result.lower()


if __name__ == "__main__":
    unittest.main()
