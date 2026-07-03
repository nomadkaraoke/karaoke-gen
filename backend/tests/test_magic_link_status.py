"""
Unit tests for UserService.get_magic_link_status.

This is a READ-ONLY status check used by the verify page to decide, on load,
whether to show the "Complete Sign-In" gate (valid link) or an "already used /
expired" message (dead link). It must NEVER consume the token — otherwise an
email link-scanner that renders the verify page would burn a valid link before
the real user clicks, which is exactly the bug this whole feature prevents.
"""
import sys
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

# Mock Firestore before importing modules that use it.
sys.modules.setdefault('google.cloud.firestore', MagicMock())
sys.modules.setdefault('google.cloud.firestore_v1', MagicMock())

from fastapi.testclient import TestClient


def _service_with_doc(mock_fs, mock_settings, doc_dict, exists=True):
    mock_settings.return_value = MagicMock(google_cloud_project='test')
    mock_db = MagicMock()
    mock_fs.Client.return_value = mock_db
    mock_doc = MagicMock()
    mock_doc.exists = exists
    mock_doc.to_dict.return_value = doc_dict
    mock_db.collection.return_value.document.return_value.get.return_value = mock_doc
    from backend.services.user_service import UserService
    return UserService(), mock_db


def _link_dict(**overrides):
    d = {
        'token': 'tok123',
        'email': 'user@example.com',
        'created_at': datetime.utcnow(),
        'expires_at': datetime.utcnow() + timedelta(hours=1),
        'used': False,
        'used_at': None,
    }
    d.update(overrides)
    return d


class TestGetMagicLinkStatus:
    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_valid_link_returns_valid(self, mock_fs, mock_settings):
        service, _ = _service_with_doc(mock_fs, mock_settings, _link_dict())
        assert service.get_magic_link_status('tok123') == 'valid'

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_valid_link_is_not_consumed(self, mock_fs, mock_settings):
        """The scanner-safety guarantee: a status check must not mutate the token."""
        service, mock_db = _service_with_doc(mock_fs, mock_settings, _link_dict())
        service.get_magic_link_status('tok123')
        doc_ref = mock_db.collection.return_value.document.return_value
        doc_ref.update.assert_not_called()
        doc_ref.set.assert_not_called()

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_used_link_returns_used(self, mock_fs, mock_settings):
        service, _ = _service_with_doc(
            mock_fs, mock_settings, _link_dict(used=True, used_at=datetime.utcnow())
        )
        assert service.get_magic_link_status('tok123') == 'used'

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_expired_link_returns_expired(self, mock_fs, mock_settings):
        service, _ = _service_with_doc(
            mock_fs, mock_settings, _link_dict(expires_at=datetime.utcnow() - timedelta(minutes=1))
        )
        assert service.get_magic_link_status('tok123') == 'expired'

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_missing_link_returns_invalid(self, mock_fs, mock_settings):
        service, _ = _service_with_doc(mock_fs, mock_settings, {}, exists=False)
        assert service.get_magic_link_status('missing') == 'invalid'

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_empty_token_returns_invalid(self, mock_fs, mock_settings):
        service, _ = _service_with_doc(mock_fs, mock_settings, _link_dict())
        assert service.get_magic_link_status('   ') == 'invalid'


class TestLinkStatusEndpoint:
    """GET /api/users/auth/link-status — thin, read-only wrapper over the service."""

    def _client(self, status_value):
        from backend.main import app
        from backend.services.user_service import get_user_service
        svc = MagicMock()
        svc.get_magic_link_status.return_value = status_value
        app.dependency_overrides[get_user_service] = lambda: svc
        return TestClient(app), app, svc

    def test_endpoint_returns_status_for_valid(self):
        client, app, svc = self._client("valid")
        try:
            r = client.get("/api/users/auth/link-status", params={"token": "tok123"})
            assert r.status_code == 200
            assert r.json()["status"] == "valid"
            svc.get_magic_link_status.assert_called_once_with("tok123")
        finally:
            app.dependency_overrides.clear()

    def test_endpoint_returns_used(self):
        client, app, svc = self._client("used")
        try:
            r = client.get("/api/users/auth/link-status", params={"token": "burned"})
            assert r.status_code == 200
            assert r.json()["status"] == "used"
        finally:
            app.dependency_overrides.clear()

    def test_endpoint_returns_invalid_for_empty_token_without_touching_firestore(self):
        client, app, svc = self._client("valid")  # service would say valid, but route must short-circuit
        try:
            r = client.get("/api/users/auth/link-status", params={"token": "   "})
            assert r.status_code == 200
            assert r.json()["status"] == "invalid"
            svc.get_magic_link_status.assert_not_called()
        finally:
            app.dependency_overrides.clear()
