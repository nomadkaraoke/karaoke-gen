"""Tests for firestore_export module."""

import unittest
from unittest.mock import MagicMock, patch


class TestFirestoreExport(unittest.TestCase):
    @patch("firestore_export.storage.Client")
    @patch("firestore_export.firestore_admin_v1.FirestoreAdminClient")
    def test_export_firestore_calls_api(self, mock_client_class, mock_storage):
        from firestore_export import export_firestore

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_operation = MagicMock()
        mock_operation.result.return_value = MagicMock()
        mock_client.export_documents.return_value = mock_operation
        mock_storage.return_value.bucket.return_value.list_blobs.return_value = []

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

    @patch("firestore_export.storage.Client")
    @patch("firestore_export.firestore_admin_v1.FirestoreAdminClient")
    def test_export_firestore_returns_summary(self, mock_client_class, mock_storage):
        from firestore_export import export_firestore

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_operation = MagicMock()
        mock_operation.result.return_value = MagicMock()
        mock_client.export_documents.return_value = mock_operation
        mock_storage.return_value.bucket.return_value.list_blobs.return_value = []

        result = export_firestore("proj", "bucket", "2026-03-29")
        assert isinstance(result, str)
        assert "2026-03-29" in result


    @patch("firestore_export.storage.Client")
    @patch("firestore_export.firestore_admin_v1.FirestoreAdminClient")
    def test_export_clears_target_prefix_first(self, mock_client_class, mock_storage):
        """A same-day re-run must delete today's leftover export before exporting,
        otherwise Firestore fails with 'Path already exists'."""
        from firestore_export import export_firestore

        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        mock_operation = MagicMock()
        mock_operation.result.return_value = MagicMock()
        mock_client.export_documents.return_value = mock_operation

        def _blob(name):
            b = MagicMock()
            b.name = name
            return b

        stale = _blob("firestore/2026-03-29/2026-03-29.overall_export_metadata")

        # First list_blobs call (target-prefix clear) returns today's stale export;
        # second call (prior-exports cleanup) returns nothing.
        mock_storage.return_value.bucket.return_value.list_blobs.side_effect = [
            [stale],
            [],
        ]

        export_firestore("proj", "test-staging", "2026-03-29")

        # The stale same-day export was deleted before the export ran.
        stale.delete.assert_called_once()
        mock_client.export_documents.assert_called_once()


class TestClearPriorExports(unittest.TestCase):
    @patch("firestore_export.storage.Client")
    def test_keeps_latest_deletes_others(self, mock_storage):
        from firestore_export import _clear_prior_exports

        def _blob(name):
            b = MagicMock()
            b.name = name
            return b

        today = _blob("firestore/2026-03-29/out.bin")
        old1 = _blob("firestore/2026-03-22/out.bin")
        old2 = _blob("firestore/2026-03-15/out.bin")
        mock_storage.return_value.bucket.return_value.list_blobs.return_value = [
            today, old1, old2,
        ]

        _clear_prior_exports("staging", "2026-03-29")

        today.delete.assert_not_called()
        old1.delete.assert_called_once()
        old2.delete.assert_called_once()

    @patch("firestore_export.storage.Client")
    def test_cleanup_failure_is_swallowed(self, mock_storage):
        from firestore_export import _clear_prior_exports

        mock_storage.return_value.bucket.return_value.list_blobs.side_effect = RuntimeError("boom")
        # Must not raise — cleanup is best-effort, the export already succeeded.
        _clear_prior_exports("staging", "2026-03-29")


if __name__ == "__main__":
    unittest.main()
