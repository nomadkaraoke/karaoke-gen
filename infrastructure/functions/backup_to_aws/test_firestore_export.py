"""Tests for firestore_export module."""

import unittest
from unittest.mock import MagicMock, patch


class TestFirestoreExport(unittest.TestCase):
    @patch("firestore_export.firestore_admin_v1.FirestoreAdminClient")
    def test_export_firestore_calls_api(self, mock_client_class):
        from firestore_export import export_firestore

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_operation = MagicMock()
        mock_operation.result.return_value = MagicMock()
        mock_client.export_documents.return_value = mock_operation

        result = export_firestore(
            project="test-project",
            staging_bucket="test-staging",
            date_str="2026-03-29",
        )

        mock_client.export_documents.assert_called_once()
        call_args = mock_client.export_documents.call_args
        request = call_args[1]["request"]
        assert request["name"] == "projects/test-project/databases/(default)"
        assert "gs://test-staging/firestore/2026-03-29" in request["output_uri_prefix"]

    @patch("firestore_export.firestore_admin_v1.FirestoreAdminClient")
    def test_export_firestore_returns_summary(self, mock_client_class):
        from firestore_export import export_firestore

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_operation = MagicMock()
        mock_operation.result.return_value = MagicMock()
        mock_client.export_documents.return_value = mock_operation

        result = export_firestore("proj", "bucket", "2026-03-29")
        assert isinstance(result, str)
        assert "2026-03-29" in result


if __name__ == "__main__":
    unittest.main()
