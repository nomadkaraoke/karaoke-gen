"""
Tests for admin-token initialization in AuthService.

An empty ADMIN_TOKENS configuration silently breaks every admin/internal-worker
endpoint (they fail closed with 403). That's safe but looks like an auth bug
rather than a config error, so it must be a LOUD signal: an ERROR log (picked up
by the error monitor → Discord alert), not a quiet warning.
"""
from unittest.mock import patch, MagicMock

from backend.services.auth_service import AuthService


def _make_service(admin_tokens_value):
    """Construct AuthService with a controlled settings.admin_tokens value."""
    with patch("backend.services.auth_service.FirestoreService"), \
         patch("backend.services.auth_service.get_settings") as mock_get_settings, \
         patch("backend.services.auth_service.logger") as mock_logger:
        mock_settings = MagicMock()
        mock_settings.admin_tokens = admin_tokens_value
        mock_settings.scheduler_service_account = "karaoke-backend@nomadkaraoke.iam.gserviceaccount.com"
        mock_get_settings.return_value = mock_settings
        service = AuthService()
        return service, mock_logger


def test_no_admin_tokens_logs_error_not_warning():
    """Empty ADMIN_TOKENS → ERROR-level loud signal, not a quiet warning."""
    service, mock_logger = _make_service("")

    assert service.admin_tokens == []
    mock_logger.error.assert_called_once()
    # It used to be a quiet warning — make sure it isn't anymore.
    mock_logger.warning.assert_not_called()


def test_admin_tokens_present_no_error():
    """Configured admin tokens → no error, tokens parsed."""
    service, mock_logger = _make_service("tok1, tok2 ,tok3")

    assert service.admin_tokens == ["tok1", "tok2", "tok3"]
    mock_logger.error.assert_not_called()
