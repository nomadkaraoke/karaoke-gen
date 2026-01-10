"""
Tests for Push Notification API routes.

Tests the /api/push/* endpoints for subscription management.
"""
import pytest
from unittest.mock import Mock, patch, AsyncMock
from fastapi.testclient import TestClient
from dataclasses import dataclass
from typing import Optional

from backend.main import app
from backend.api.dependencies import require_auth, require_admin
from backend.services.auth_service import UserType, AuthResult


@pytest.fixture
def client():
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def mock_auth_result():
    """Create a mock AuthResult for regular user."""
    return AuthResult(
        is_valid=True,
        user_type=UserType.UNLIMITED,
        remaining_uses=-1,
        message="Valid",
        user_email="test@example.com",
        is_admin=False,
    )


@pytest.fixture
def mock_admin_auth_result():
    """Create a mock AuthResult for admin user."""
    return AuthResult(
        is_valid=True,
        user_type=UserType.ADMIN,
        remaining_uses=-1,
        message="Valid",
        user_email="admin@nomadkaraoke.com",
        is_admin=True,
    )


class TestGetVapidPublicKey:
    """Tests for GET /api/push/vapid-public-key."""

    def test_returns_disabled_when_feature_off(self, client):
        """Returns enabled=false when push notifications disabled."""
        with patch('backend.api.routes.push.get_settings') as mock_settings:
            mock_settings.return_value.enable_push_notifications = False

            response = client.get("/api/push/vapid-public-key")

            assert response.status_code == 200
            data = response.json()
            assert data["enabled"] is False
            assert data["vapid_public_key"] is None

    def test_returns_key_when_enabled(self, client):
        """Returns public key when push notifications enabled."""
        with patch('backend.api.routes.push.get_settings') as mock_settings, \
             patch('backend.api.routes.push.get_push_notification_service') as mock_service:
            mock_settings.return_value.enable_push_notifications = True
            mock_service.return_value.get_public_key.return_value = "test-public-key-123"

            response = client.get("/api/push/vapid-public-key")

            assert response.status_code == 200
            data = response.json()
            assert data["enabled"] is True
            assert data["vapid_public_key"] == "test-public-key-123"


class TestSubscribe:
    """Tests for POST /api/push/subscribe."""

    def test_requires_authentication(self, client):
        """Returns 401 when not authenticated."""
        # Clear any auth overrides to test real auth
        app.dependency_overrides.clear()

        response = client.post(
            "/api/push/subscribe",
            json={
                "endpoint": "https://push.example.com/endpoint",
                "keys": {"p256dh": "key1", "auth": "key2"}
            }
        )

        assert response.status_code == 401

    def test_returns_503_when_disabled(self, client, mock_auth_result):
        """Returns 503 when push notifications disabled."""
        async def override_auth():
            return mock_auth_result

        app.dependency_overrides[require_auth] = override_auth

        try:
            with patch('backend.api.routes.push.get_settings') as mock_settings:
                mock_settings.return_value.enable_push_notifications = False

                response = client.post(
                    "/api/push/subscribe",
                    json={
                        "endpoint": "https://push.example.com/endpoint",
                        "keys": {"p256dh": "key1", "auth": "key2"}
                    }
                )

                assert response.status_code == 503
                assert "not enabled" in response.json()["detail"].lower()
        finally:
            app.dependency_overrides.clear()

    def test_validates_required_keys(self, client, mock_auth_result):
        """Returns 400 when keys missing."""
        async def override_auth():
            return mock_auth_result

        app.dependency_overrides[require_auth] = override_auth

        try:
            with patch('backend.api.routes.push.get_settings') as mock_settings:
                mock_settings.return_value.enable_push_notifications = True

                response = client.post(
                    "/api/push/subscribe",
                    json={
                        "endpoint": "https://push.example.com/endpoint",
                        "keys": {"p256dh": "key1"}  # Missing auth
                    }
                )

                assert response.status_code == 400
                assert "missing" in response.json()["detail"].lower()
        finally:
            app.dependency_overrides.clear()

    def test_successful_subscription(self, client, mock_auth_result):
        """Successfully subscribes user to push notifications."""
        async def override_auth():
            return mock_auth_result

        app.dependency_overrides[require_auth] = override_auth

        try:
            with patch('backend.api.routes.push.get_settings') as mock_settings, \
                 patch('backend.api.routes.push.get_push_notification_service') as mock_service:
                mock_settings.return_value.enable_push_notifications = True
                mock_service.return_value.add_subscription = AsyncMock(return_value=True)

                response = client.post(
                    "/api/push/subscribe",
                    json={
                        "endpoint": "https://push.example.com/endpoint",
                        "keys": {"p256dh": "key1", "auth": "key2"},
                        "device_name": "Test Device"
                    }
                )

                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "success"
                mock_service.return_value.add_subscription.assert_called_once_with(
                    user_email="test@example.com",
                    endpoint="https://push.example.com/endpoint",
                    keys={"p256dh": "key1", "auth": "key2"},
                    device_name="Test Device"
                )
        finally:
            app.dependency_overrides.clear()


class TestUnsubscribe:
    """Tests for POST /api/push/unsubscribe."""

    def test_requires_authentication(self, client):
        """Returns 401 when not authenticated."""
        app.dependency_overrides.clear()

        response = client.post(
            "/api/push/unsubscribe",
            json={"endpoint": "https://push.example.com/endpoint"}
        )

        assert response.status_code == 401

    def test_successful_unsubscribe(self, client, mock_auth_result):
        """Successfully unsubscribes from push notifications."""
        async def override_auth():
            return mock_auth_result

        app.dependency_overrides[require_auth] = override_auth

        try:
            with patch('backend.api.routes.push.get_push_notification_service') as mock_service:
                mock_service.return_value.remove_subscription = AsyncMock(return_value=True)

                response = client.post(
                    "/api/push/unsubscribe",
                    json={"endpoint": "https://push.example.com/endpoint"}
                )

                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "success"
                mock_service.return_value.remove_subscription.assert_called_once()
        finally:
            app.dependency_overrides.clear()

    def test_unsubscribe_not_found_succeeds(self, client, mock_auth_result):
        """Returns success even when subscription not found."""
        async def override_auth():
            return mock_auth_result

        app.dependency_overrides[require_auth] = override_auth

        try:
            with patch('backend.api.routes.push.get_push_notification_service') as mock_service:
                mock_service.return_value.remove_subscription = AsyncMock(return_value=False)

                response = client.post(
                    "/api/push/unsubscribe",
                    json={"endpoint": "https://push.example.com/unknown"}
                )

                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "success"
        finally:
            app.dependency_overrides.clear()


class TestListSubscriptions:
    """Tests for GET /api/push/subscriptions."""

    def test_requires_authentication(self, client):
        """Returns 401 when not authenticated."""
        app.dependency_overrides.clear()

        response = client.get("/api/push/subscriptions")
        assert response.status_code == 401

    def test_returns_user_subscriptions(self, client, mock_auth_result):
        """Returns list of user's subscriptions."""
        async def override_auth():
            return mock_auth_result

        app.dependency_overrides[require_auth] = override_auth

        try:
            with patch('backend.api.routes.push.get_push_notification_service') as mock_service:
                mock_service.return_value.list_subscriptions = AsyncMock(return_value=[
                    {
                        "endpoint": "https://push.example.com/endpoint1",
                        "device_name": "Device 1",
                        "created_at": "2024-01-01T00:00:00Z",
                        "last_used_at": None
                    },
                    {
                        "endpoint": "https://push.example.com/endpoint2",
                        "device_name": "Device 2",
                        "created_at": "2024-01-02T00:00:00Z",
                        "last_used_at": "2024-01-03T00:00:00Z"
                    }
                ])

                response = client.get("/api/push/subscriptions")

                assert response.status_code == 200
                data = response.json()
                assert data["count"] == 2
                assert len(data["subscriptions"]) == 2
                assert data["subscriptions"][0]["device_name"] == "Device 1"
        finally:
            app.dependency_overrides.clear()


class TestTestNotification:
    """Tests for POST /api/push/test."""

    def test_requires_admin(self, client, mock_auth_result):
        """Returns 403 when not admin."""
        # Use a non-admin auth result
        async def override_admin():
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="Admin access required")

        app.dependency_overrides[require_admin] = override_admin

        try:
            response = client.post(
                "/api/push/test",
                json={"title": "Test", "body": "Test message"}
            )

            assert response.status_code == 403
        finally:
            app.dependency_overrides.clear()

    def test_returns_503_when_disabled(self, client, mock_admin_auth_result):
        """Returns 503 when push notifications disabled."""
        async def override_admin():
            return mock_admin_auth_result

        app.dependency_overrides[require_admin] = override_admin

        try:
            with patch('backend.api.routes.push.get_settings') as mock_settings:
                mock_settings.return_value.enable_push_notifications = False

                response = client.post(
                    "/api/push/test",
                    json={"title": "Test", "body": "Test message"}
                )

                assert response.status_code == 503
        finally:
            app.dependency_overrides.clear()

    def test_sends_test_notification(self, client, mock_admin_auth_result):
        """Successfully sends test notification to admin."""
        async def override_admin():
            return mock_admin_auth_result

        app.dependency_overrides[require_admin] = override_admin

        try:
            with patch('backend.api.routes.push.get_settings') as mock_settings, \
                 patch('backend.api.routes.push.get_push_notification_service') as mock_service:
                mock_settings.return_value.enable_push_notifications = True
                mock_service.return_value.send_push = AsyncMock(return_value=2)

                response = client.post(
                    "/api/push/test",
                    json={"title": "Test Title", "body": "Test Body"}
                )

                assert response.status_code == 200
                data = response.json()
                assert data["status"] == "success"
                assert data["sent_count"] == 2
                mock_service.return_value.send_push.assert_called_once_with(
                    user_email="admin@nomadkaraoke.com",
                    title="Test Title",
                    body="Test Body",
                    url="/app/",
                    tag="test"
                )
        finally:
            app.dependency_overrides.clear()
