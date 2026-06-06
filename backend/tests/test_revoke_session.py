"""
Tests for UserService.revoke_session idempotency.

Revoking a session that no longer exists (already logged out, expired, or
cleaned up) should be treated as success rather than logged as an error.
Firestore's document.update() raises NotFound when the document is absent.
"""
from unittest.mock import MagicMock, patch

# Mock Firestore before importing modules that use it
import sys

sys.modules.setdefault('google.cloud.firestore', MagicMock())
sys.modules.setdefault('google.cloud.firestore_v1', MagicMock())

from google.api_core import exceptions as google_exceptions


class TestRevokeSession:
    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_revoke_existing_session_succeeds(self, mock_fs, mock_settings):
        """A normal revoke updates is_active and returns True."""
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db
        doc_ref = mock_db.collection.return_value.document.return_value

        from backend.services.user_service import UserService

        service = UserService()
        assert service.revoke_session("token-abc") is True
        doc_ref.update.assert_called_once_with({'is_active': False})

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_revoke_missing_session_is_idempotent(self, mock_fs, mock_settings):
        """A missing session document is already revoked → return True, no error."""
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db
        doc_ref = mock_db.collection.return_value.document.return_value
        doc_ref.update.side_effect = google_exceptions.NotFound(
            "No document to update: sessions/deadbeef"
        )

        from backend.services.user_service import UserService

        service = UserService()
        assert service.revoke_session("missing-token") is True

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_revoke_unexpected_error_returns_false(self, mock_fs, mock_settings):
        """Genuine errors (not NotFound) are swallowed and return False."""
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db
        doc_ref = mock_db.collection.return_value.document.return_value
        doc_ref.update.side_effect = google_exceptions.DeadlineExceeded("timeout")

        from backend.services.user_service import UserService

        service = UserService()
        assert service.revoke_session("token-abc") is False
