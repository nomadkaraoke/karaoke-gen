"""
Unit tests for rate limits admin API endpoints.

Tests the rate limit statistics, blocklist management, and user override endpoints.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from fastapi.testclient import TestClient
from fastapi import FastAPI
from datetime import datetime, timezone

from backend.api.routes.rate_limits import router
from backend.api.dependencies import require_admin
from backend.services.auth_service import AuthResult, UserType


# Create a test app with the rate_limits router
app = FastAPI()
app.include_router(router, prefix="/api")


def get_mock_admin():
    """Override for require_admin dependency."""
    return AuthResult(
        is_valid=True,
        user_type=UserType.ADMIN,
        remaining_uses=-1,
        message="Admin access granted",
        user_email="admin@example.com",
        is_admin=True,
    )


# Override the require_admin dependency
app.dependency_overrides[require_admin] = get_mock_admin


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def mock_settings():
    """Create mock settings."""
    settings = Mock()
    settings.enable_rate_limiting = True
    settings.rate_limit_jobs_per_day = 5
    settings.rate_limit_youtube_uploads_per_day = 10
    settings.rate_limit_beta_ip_per_day = 1
    return settings


class TestGetRateLimitStats:
    """Tests for GET /api/admin/rate-limits/stats endpoint."""

    def test_returns_stats(self, client, mock_settings):
        """Test successful stats retrieval."""
        with patch('backend.api.routes.rate_limits.get_rate_limit_service') as mock_get_rls, \
             patch('backend.api.routes.rate_limits.get_email_validation_service') as mock_get_evs, \
             patch('backend.services.youtube_quota_service.get_youtube_quota_service') as mock_get_qs, \
             patch('backend.services.youtube_upload_queue_service.get_youtube_upload_queue_service') as mock_get_uqs, \
             patch('backend.api.routes.rate_limits.settings', mock_settings):

            # Setup rate limit service mock
            mock_rls = Mock()
            mock_rls.get_youtube_uploads_today.return_value = 3
            mock_rls.get_all_overrides.return_value = {"user1@example.com": {}}
            mock_get_rls.return_value = mock_rls

            # Setup email validation service mock
            mock_evs = Mock()
            mock_evs.get_blocklist_stats.return_value = {
                "disposable_domains_count": 100,
                "blocked_emails_count": 5,
                "blocked_ips_count": 2,
                "default_disposable_domains_count": 130,
            }
            mock_get_evs.return_value = mock_evs

            # Setup quota service mock
            mock_quota = Mock()
            mock_quota.get_quota_stats.return_value = {
                "units_consumed": 900,
                "units_remaining": 8600,
                "units_limit": 10000,
                "effective_limit": 9500,
                "upload_cost": 300,
                "estimated_uploads_remaining": 28,
                "seconds_until_reset": 43200,
            }
            mock_get_qs.return_value = mock_quota

            # Setup queue service mock
            mock_queue = Mock()
            mock_queue.get_queue_stats.return_value = {
                "queued": 0,
                "processing": 0,
                "failed": 0,
                "completed": 0,
                "total": 0,
            }
            mock_get_uqs.return_value = mock_queue

            response = client.get(
                "/api/admin/rate-limits/stats",
                headers={"Authorization": "Bearer admin-token"}
            )

            assert response.status_code == 200
            data = response.json()
            assert data["jobs_per_day_limit"] == 5
            assert data["youtube_uploads_per_day_limit"] == 10
            assert data["youtube_uploads_today"] == 3
            assert data["youtube_uploads_remaining"] == 7
            assert data["disposable_domains_count"] == 100
            assert data["total_overrides"] == 1
            # New quota fields
            assert data["youtube_quota_units_consumed"] == 900
            assert data["youtube_quota_units_remaining"] == 8600
            assert data["youtube_quota_daily_limit"] == 10000


class TestGetUserRateLimitStatus:
    """Tests for GET /api/admin/rate-limits/users/{email} endpoint."""

    def test_returns_user_status(self, client, mock_settings):
        """Test successful user status retrieval."""
        with patch('backend.api.routes.rate_limits.get_rate_limit_service') as mock_get_rls, \
             patch('backend.api.routes.rate_limits.settings', mock_settings):

            mock_rls = Mock()
            mock_rls.get_user_job_count_today.return_value = 2
            mock_rls.get_user_override.return_value = None
            mock_get_rls.return_value = mock_rls

            response = client.get(
                "/api/admin/rate-limits/users/user@example.com",
                headers={"Authorization": "Bearer admin-token"}
            )

            assert response.status_code == 200
            data = response.json()
            assert data["email"] == "user@example.com"
            assert data["jobs_today"] == 2
            assert data["jobs_limit"] == 5
            assert data["jobs_remaining"] == 3
            assert data["has_bypass"] is False

    def test_returns_user_with_bypass(self, client, mock_settings):
        """Test user with bypass override."""
        with patch('backend.api.routes.rate_limits.get_rate_limit_service') as mock_get_rls, \
             patch('backend.api.routes.rate_limits.settings', mock_settings):

            mock_rls = Mock()
            mock_rls.get_user_job_count_today.return_value = 10
            mock_rls.get_user_override.return_value = {
                "bypass_job_limit": True,
                "reason": "VIP user"
            }
            mock_get_rls.return_value = mock_rls

            response = client.get(
                "/api/admin/rate-limits/users/vip@example.com",
                headers={"Authorization": "Bearer admin-token"}
            )

            assert response.status_code == 200
            data = response.json()
            assert data["has_bypass"] is True
            assert data["bypass_reason"] == "VIP user"
            assert data["jobs_remaining"] == -1  # Unlimited


class TestBlocklistEndpoints:
    """Tests for blocklist management endpoints."""

    def test_get_blocklists(self, client):
        """Test getting all blocklists."""
        with patch('backend.api.routes.rate_limits.get_email_validation_service') as mock_get_evs, \
             patch('backend.services.firestore_service.get_firestore_client') as mock_get_db:

            mock_evs = Mock()
            mock_evs.get_blocklist_config.return_value = {
                "disposable_domains": {"tempmail.com", "mailinator.com"},
                "blocked_emails": {"spammer@example.com"},
                "blocked_ips": {"192.168.1.100"},
            }
            mock_get_evs.return_value = mock_evs

            # Mock Firestore for metadata
            mock_db = Mock()
            mock_doc = Mock()
            mock_doc.exists = True
            mock_doc.to_dict.return_value = {
                "updated_at": datetime.now(timezone.utc),
                "updated_by": "admin@example.com"
            }
            mock_db.collection.return_value.document.return_value.get.return_value = mock_doc
            mock_get_db.return_value = mock_db

            response = client.get(
                "/api/admin/rate-limits/blocklists",
                headers={"Authorization": "Bearer admin-token"}
            )

            assert response.status_code == 200
            data = response.json()
            assert "tempmail.com" in data["disposable_domains"]
            assert "spammer@example.com" in data["blocked_emails"]

    def test_add_disposable_domain(self, client):
        """Test adding a disposable domain."""
        with patch('backend.api.routes.rate_limits.get_email_validation_service') as mock_get_evs:

            mock_evs = Mock()
            mock_evs.add_disposable_domain.return_value = True
            mock_get_evs.return_value = mock_evs

            response = client.post(
                "/api/admin/rate-limits/blocklists/disposable-domains",
                json={"domain": "newtemp.com"},
                headers={"Authorization": "Bearer admin-token"}
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            mock_evs.add_disposable_domain.assert_called_once_with("newtemp.com", "admin@example.com")

    def test_add_disposable_domain_invalid(self, client):
        """Test adding invalid domain."""
        response = client.post(
            "/api/admin/rate-limits/blocklists/disposable-domains",
            json={"domain": "invalid"},  # No dot
            headers={"Authorization": "Bearer admin-token"}
        )

        assert response.status_code == 400

    def test_remove_disposable_domain(self, client):
        """Test removing a disposable domain."""
        with patch('backend.api.routes.rate_limits.get_email_validation_service') as mock_get_evs:

            mock_evs = Mock()
            mock_evs.remove_disposable_domain.return_value = True
            mock_get_evs.return_value = mock_evs

            response = client.delete(
                "/api/admin/rate-limits/blocklists/disposable-domains/tempmail.com",
                headers={"Authorization": "Bearer admin-token"}
            )

            assert response.status_code == 200
            mock_evs.remove_disposable_domain.assert_called_once()

    def test_remove_disposable_domain_not_found(self, client):
        """Test removing non-existent domain."""
        with patch('backend.api.routes.rate_limits.get_email_validation_service') as mock_get_evs:

            mock_evs = Mock()
            mock_evs.remove_disposable_domain.return_value = False
            mock_get_evs.return_value = mock_evs

            response = client.delete(
                "/api/admin/rate-limits/blocklists/disposable-domains/notfound.com",
                headers={"Authorization": "Bearer admin-token"}
            )

            assert response.status_code == 404

    def test_add_blocked_email(self, client):
        """Test adding a blocked email."""
        with patch('backend.api.routes.rate_limits.get_email_validation_service') as mock_get_evs:

            mock_evs = Mock()
            mock_evs.add_blocked_email.return_value = True
            mock_get_evs.return_value = mock_evs

            response = client.post(
                "/api/admin/rate-limits/blocklists/blocked-emails",
                json={"email": "spammer@example.com"},
                headers={"Authorization": "Bearer admin-token"}
            )

            assert response.status_code == 200

    def test_add_blocked_ip(self, client):
        """Test adding a blocked IP."""
        with patch('backend.api.routes.rate_limits.get_email_validation_service') as mock_get_evs:

            mock_evs = Mock()
            mock_evs.add_blocked_ip.return_value = True
            mock_get_evs.return_value = mock_evs

            response = client.post(
                "/api/admin/rate-limits/blocklists/blocked-ips",
                json={"ip_address": "192.168.1.100"},
                headers={"Authorization": "Bearer admin-token"}
            )

            assert response.status_code == 200


class TestUserOverrideEndpoints:
    """Tests for user override management endpoints."""

    def test_get_all_overrides(self, client):
        """Test getting all user overrides."""
        with patch('backend.api.routes.rate_limits.get_rate_limit_service') as mock_get_rls:

            mock_rls = Mock()
            mock_rls.get_all_overrides.return_value = {
                "vip@example.com": {
                    "bypass_job_limit": True,
                    "custom_daily_job_limit": None,
                    "reason": "VIP user",
                    "created_by": "admin@example.com",
                    "created_at": datetime.now(timezone.utc),
                }
            }
            mock_get_rls.return_value = mock_rls

            response = client.get(
                "/api/admin/rate-limits/overrides",
                headers={"Authorization": "Bearer admin-token"}
            )

            assert response.status_code == 200
            data = response.json()
            assert data["total"] == 1
            assert len(data["overrides"]) == 1
            assert data["overrides"][0]["email"] == "vip@example.com"

    def test_set_user_override(self, client):
        """Test setting a user override."""
        with patch('backend.api.routes.rate_limits.get_rate_limit_service') as mock_get_rls:

            mock_rls = Mock()
            mock_get_rls.return_value = mock_rls

            response = client.put(
                "/api/admin/rate-limits/overrides/user@example.com",
                json={
                    "bypass_job_limit": True,
                    "reason": "Special access granted"
                },
                headers={"Authorization": "Bearer admin-token"}
            )

            assert response.status_code == 200
            mock_rls.set_user_override.assert_called_once_with(
                user_email="user@example.com",
                bypass_job_limit=True,
                custom_daily_job_limit=None,
                reason="Special access granted",
                admin_email="admin@example.com",
            )

    def test_set_user_override_with_custom_limit(self, client):
        """Test setting a user override with custom limit."""
        with patch('backend.api.routes.rate_limits.get_rate_limit_service') as mock_get_rls:

            mock_rls = Mock()
            mock_get_rls.return_value = mock_rls

            response = client.put(
                "/api/admin/rate-limits/overrides/user@example.com",
                json={
                    "bypass_job_limit": False,
                    "custom_daily_job_limit": 20,
                    "reason": "High volume user"
                },
                headers={"Authorization": "Bearer admin-token"}
            )

            assert response.status_code == 200
            mock_rls.set_user_override.assert_called_once()

    def test_set_user_override_missing_reason(self, client):
        """Test setting override without reason fails."""
        response = client.put(
            "/api/admin/rate-limits/overrides/user@example.com",
            json={
                "bypass_job_limit": True,
                "reason": "ab"  # Too short
            },
            headers={"Authorization": "Bearer admin-token"}
        )

        assert response.status_code == 400

    def test_remove_user_override(self, client):
        """Test removing a user override."""
        with patch('backend.api.routes.rate_limits.get_rate_limit_service') as mock_get_rls:

            mock_rls = Mock()
            mock_rls.remove_user_override.return_value = True
            mock_get_rls.return_value = mock_rls

            response = client.delete(
                "/api/admin/rate-limits/overrides/user@example.com",
                headers={"Authorization": "Bearer admin-token"}
            )

            assert response.status_code == 200

    def test_remove_user_override_not_found(self, client):
        """Test removing non-existent override."""
        with patch('backend.api.routes.rate_limits.get_rate_limit_service') as mock_get_rls:

            mock_rls = Mock()
            mock_rls.remove_user_override.return_value = False
            mock_get_rls.return_value = mock_rls

            response = client.delete(
                "/api/admin/rate-limits/overrides/notfound@example.com",
                headers={"Authorization": "Bearer admin-token"}
            )

            assert response.status_code == 404


class TestYouTubeQueueEndpoints:
    """Tests for YouTube upload queue management endpoints."""

    def test_get_youtube_queue(self, client):
        """Test getting YouTube queue entries."""
        with patch('backend.services.youtube_upload_queue_service.get_youtube_upload_queue_service') as mock_get_qs:

            mock_qs = Mock()
            mock_qs.get_all_queue_entries.return_value = [
                {
                    "job_id": "job-123",
                    "status": "queued",
                    "reason": "quota_exceeded",
                    "user_email": "user@example.com",
                    "artist": "Test Artist",
                    "title": "Test Song",
                    "brand_code": "NOMAD-100",
                    "queued_at": "2026-03-03T10:00:00",
                    "attempts": 0,
                    "max_attempts": 5,
                    "last_error": None,
                    "youtube_url": None,
                    "notification_sent": False,
                }
            ]
            mock_qs.get_queue_stats.return_value = {
                "queued": 1,
                "processing": 0,
                "failed": 0,
                "completed": 5,
                "total": 6,
            }
            mock_get_qs.return_value = mock_qs

            response = client.get(
                "/api/admin/rate-limits/youtube-queue",
                headers={"Authorization": "Bearer admin-token"}
            )

            assert response.status_code == 200
            data = response.json()
            assert len(data["entries"]) == 1
            assert data["entries"][0]["job_id"] == "job-123"
            assert data["entries"][0]["status"] == "queued"
            assert data["stats"]["queued"] == 1
            assert data["stats"]["total"] == 6

    def test_retry_youtube_upload_success(self, client):
        """Test retrying a failed YouTube upload."""
        with patch('backend.services.youtube_upload_queue_service.get_youtube_upload_queue_service') as mock_get_qs:

            mock_qs = Mock()
            mock_qs.retry_upload.return_value = True
            mock_get_qs.return_value = mock_qs

            response = client.post(
                "/api/admin/rate-limits/youtube-queue/job-123/retry",
                headers={"Authorization": "Bearer admin-token"}
            )

            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            mock_qs.retry_upload.assert_called_once_with("job-123")

    def test_retry_youtube_upload_not_found(self, client):
        """Test retrying a non-existent or non-retryable upload."""
        with patch('backend.services.youtube_upload_queue_service.get_youtube_upload_queue_service') as mock_get_qs:

            mock_qs = Mock()
            mock_qs.retry_upload.return_value = False
            mock_get_qs.return_value = mock_qs

            response = client.post(
                "/api/admin/rate-limits/youtube-queue/nonexistent/retry",
                headers={"Authorization": "Bearer admin-token"}
            )

            assert response.status_code == 404

    def test_trigger_queue_processing(self, client):
        """Test manually triggering queue processing."""
        response = client.post(
            "/api/admin/rate-limits/youtube-queue/process",
            headers={"Authorization": "Bearer admin-token"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "background" in data["message"].lower()
