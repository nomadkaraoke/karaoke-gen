"""
Tests for PushNotificationService.

Tests the Web Push notification sending and subscription management.
"""
import pytest
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from datetime import datetime, timezone

from backend.services.push_notification_service import (
    PushNotificationService,
    get_push_notification_service,
    SubscriptionGoneError,
)


@pytest.fixture
def mock_settings():
    """Mock settings with push notifications enabled."""
    settings = Mock()
    settings.enable_push_notifications = True
    settings.max_push_subscriptions_per_user = 5
    settings.vapid_subject = "mailto:test@example.com"
    settings.get_secret = Mock(side_effect=lambda x: {
        "vapid-public-key": "test-public-key",
        "vapid-private-key": "test-private-key"
    }.get(x))
    return settings


@pytest.fixture
def mock_db():
    """Mock Firestore client."""
    return Mock()


@pytest.fixture
def push_service(mock_settings, mock_db):
    """Create PushNotificationService with mocked dependencies."""
    with patch('backend.services.push_notification_service.get_settings', return_value=mock_settings):
        service = PushNotificationService(db=mock_db)
        return service


class TestPushNotificationServiceInit:
    """Tests for service initialization and configuration."""

    def test_is_enabled_when_configured(self, push_service):
        """Service reports enabled when all config present."""
        assert push_service.is_enabled() is True

    def test_is_disabled_when_feature_flag_off(self, mock_db):
        """Service reports disabled when feature flag off."""
        settings = Mock()
        settings.enable_push_notifications = False
        settings.get_secret = Mock(return_value="key")

        with patch('backend.services.push_notification_service.get_settings', return_value=settings):
            service = PushNotificationService(db=mock_db)
            assert service.is_enabled() is False

    def test_is_disabled_when_vapid_keys_missing(self, mock_db):
        """Service reports disabled when VAPID keys missing."""
        settings = Mock()
        settings.enable_push_notifications = True
        settings.get_secret = Mock(return_value=None)

        with patch('backend.services.push_notification_service.get_settings', return_value=settings):
            service = PushNotificationService(db=mock_db)
            assert service.is_enabled() is False

    def test_get_public_key(self, push_service):
        """Service returns public key when enabled."""
        assert push_service.get_public_key() == "test-public-key"

    def test_get_public_key_returns_none_when_disabled(self, mock_db):
        """Service returns None for public key when disabled."""
        settings = Mock()
        settings.enable_push_notifications = False
        settings.get_secret = Mock(return_value="key")

        with patch('backend.services.push_notification_service.get_settings', return_value=settings):
            service = PushNotificationService(db=mock_db)
            assert service.get_public_key() is None


class TestSendPush:
    """Tests for sending push notifications."""

    @pytest.mark.asyncio
    async def test_send_push_skips_when_disabled(self, mock_db):
        """send_push returns 0 when push notifications disabled."""
        settings = Mock()
        settings.enable_push_notifications = False
        settings.get_secret = Mock(return_value=None)

        with patch('backend.services.push_notification_service.get_settings', return_value=settings):
            service = PushNotificationService(db=mock_db)
            result = await service.send_push("test@example.com", "Title", "Body")
            assert result == 0

    @pytest.mark.asyncio
    async def test_send_push_no_user(self, push_service):
        """send_push returns 0 when user not found."""
        # Mock user not existing
        push_service.db.collection.return_value.document.return_value.get.return_value.exists = False

        result = await push_service.send_push("unknown@example.com", "Title", "Body")
        assert result == 0

    @pytest.mark.asyncio
    async def test_send_push_no_subscriptions(self, push_service):
        """send_push returns 0 when user has no subscriptions."""
        # Mock user with no subscriptions
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"push_subscriptions": []}
        push_service.db.collection.return_value.document.return_value.get.return_value = mock_doc

        result = await push_service.send_push("test@example.com", "Title", "Body")
        assert result == 0

    @pytest.mark.asyncio
    async def test_send_push_success(self, push_service):
        """send_push successfully sends to subscription."""
        # Mock user with subscription
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "push_subscriptions": [{
                "endpoint": "https://push.example.com/endpoint",
                "keys": {"p256dh": "key1", "auth": "key2"},
                "device_name": "Test Device"
            }]
        }
        push_service.db.collection.return_value.document.return_value.get.return_value = mock_doc

        with patch('backend.services.push_notification_service.webpush') as mock_webpush:
            result = await push_service.send_push("test@example.com", "Title", "Body")

            assert result == 1
            mock_webpush.assert_called_once()
            call_args = mock_webpush.call_args
            assert call_args[1]["subscription_info"]["endpoint"] == "https://push.example.com/endpoint"

    @pytest.mark.asyncio
    async def test_send_push_removes_gone_subscription(self, push_service):
        """send_push removes subscription when 410 Gone returned."""
        from pywebpush import WebPushException

        # Mock user with subscription
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "push_subscriptions": [{
                "endpoint": "https://push.example.com/endpoint",
                "keys": {"p256dh": "key1", "auth": "key2"},
                "device_name": "Test Device"
            }]
        }
        push_service.db.collection.return_value.document.return_value.get.return_value = mock_doc

        # Mock webpush to raise 410 error
        mock_response = Mock()
        mock_response.status_code = 410
        error = WebPushException("Gone", response=mock_response)

        with patch('backend.services.push_notification_service.webpush', side_effect=error):
            result = await push_service.send_push("test@example.com", "Title", "Body")

            assert result == 0
            # Verify invalid subscription was cleaned up
            push_service.db.collection.return_value.document.return_value.update.assert_called()


class TestSubscriptionManagement:
    """Tests for adding, removing, and listing subscriptions."""

    @pytest.mark.asyncio
    async def test_add_subscription_new_user(self, push_service):
        """add_subscription returns False for non-existent user."""
        push_service.db.collection.return_value.document.return_value.get.return_value.exists = False

        result = await push_service.add_subscription(
            "unknown@example.com",
            "https://push.example.com/endpoint",
            {"p256dh": "key1", "auth": "key2"},
            "Test Device"
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_add_subscription_success(self, push_service):
        """add_subscription adds new subscription."""
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"push_subscriptions": []}
        push_service.db.collection.return_value.document.return_value.get.return_value = mock_doc

        result = await push_service.add_subscription(
            "test@example.com",
            "https://push.example.com/endpoint",
            {"p256dh": "key1", "auth": "key2"},
            "Test Device"
        )

        assert result is True
        push_service.db.collection.return_value.document.return_value.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_subscription_updates_existing(self, push_service):
        """add_subscription updates existing subscription with same endpoint."""
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "push_subscriptions": [{
                "endpoint": "https://push.example.com/endpoint",
                "keys": {"p256dh": "old-key", "auth": "old-auth"},
                "device_name": "Old Device"
            }]
        }
        push_service.db.collection.return_value.document.return_value.get.return_value = mock_doc

        result = await push_service.add_subscription(
            "test@example.com",
            "https://push.example.com/endpoint",
            {"p256dh": "new-key", "auth": "new-auth"},
            "New Device"
        )

        assert result is True
        # Verify update was called (subscription replaced, not added)
        update_call = push_service.db.collection.return_value.document.return_value.update.call_args
        subs = update_call[0][0]["push_subscriptions"]
        assert len(subs) == 1
        assert subs[0]["device_name"] == "New Device"

    @pytest.mark.asyncio
    async def test_add_subscription_enforces_max_limit(self, push_service):
        """add_subscription removes oldest when max exceeded."""
        # Create 5 existing subscriptions
        existing_subs = [
            {
                "endpoint": f"https://push.example.com/endpoint{i}",
                "keys": {"p256dh": "key", "auth": "auth"},
                "device_name": f"Device {i}",
                "created_at": f"2024-01-0{i+1}T00:00:00Z"
            }
            for i in range(5)
        ]

        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"push_subscriptions": existing_subs}
        push_service.db.collection.return_value.document.return_value.get.return_value = mock_doc

        result = await push_service.add_subscription(
            "test@example.com",
            "https://push.example.com/new-endpoint",
            {"p256dh": "new-key", "auth": "new-auth"},
            "New Device"
        )

        assert result is True
        # Verify oldest was removed (max 5 subscriptions)
        update_call = push_service.db.collection.return_value.document.return_value.update.call_args
        subs = update_call[0][0]["push_subscriptions"]
        assert len(subs) == 5

    @pytest.mark.asyncio
    async def test_remove_subscription_success(self, push_service):
        """remove_subscription removes existing subscription."""
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "push_subscriptions": [{
                "endpoint": "https://push.example.com/endpoint",
                "keys": {"p256dh": "key", "auth": "auth"},
                "device_name": "Test Device"
            }]
        }
        push_service.db.collection.return_value.document.return_value.get.return_value = mock_doc

        result = await push_service.remove_subscription(
            "test@example.com",
            "https://push.example.com/endpoint"
        )

        assert result is True
        update_call = push_service.db.collection.return_value.document.return_value.update.call_args
        subs = update_call[0][0]["push_subscriptions"]
        assert len(subs) == 0

    @pytest.mark.asyncio
    async def test_remove_subscription_not_found(self, push_service):
        """remove_subscription returns False when subscription not found."""
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"push_subscriptions": []}
        push_service.db.collection.return_value.document.return_value.get.return_value = mock_doc

        result = await push_service.remove_subscription(
            "test@example.com",
            "https://push.example.com/unknown-endpoint"
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_list_subscriptions_success(self, push_service):
        """list_subscriptions returns user's subscriptions."""
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "push_subscriptions": [
                {
                    "endpoint": "https://push.example.com/endpoint1",
                    "keys": {"p256dh": "key", "auth": "auth"},
                    "device_name": "Device 1",
                    "created_at": "2024-01-01T00:00:00Z",
                    "last_used_at": None
                },
                {
                    "endpoint": "https://push.example.com/endpoint2",
                    "keys": {"p256dh": "key", "auth": "auth"},
                    "device_name": "Device 2",
                    "created_at": "2024-01-02T00:00:00Z",
                    "last_used_at": "2024-01-03T00:00:00Z"
                }
            ]
        }
        push_service.db.collection.return_value.document.return_value.get.return_value = mock_doc

        result = await push_service.list_subscriptions("test@example.com")

        assert len(result) == 2
        assert result[0]["device_name"] == "Device 1"
        assert result[1]["device_name"] == "Device 2"
        # Verify keys are NOT included in response (security)
        assert "keys" not in result[0]


class TestNotificationFormatting:
    """Tests for blocking and completion notification formatting."""

    @pytest.mark.asyncio
    async def test_send_blocking_notification_lyrics(self, push_service):
        """send_blocking_notification formats lyrics review notification."""
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "push_subscriptions": [{
                "endpoint": "https://push.example.com/endpoint",
                "keys": {"p256dh": "key", "auth": "auth"}
            }]
        }
        push_service.db.collection.return_value.document.return_value.get.return_value = mock_doc

        job = {
            "job_id": "test-job-123",
            "user_email": "test@example.com",
            "artist": "Test Artist",
            "title": "Test Song"
        }

        with patch('backend.services.push_notification_service.webpush') as mock_webpush:
            await push_service.send_blocking_notification(job, "lyrics")

            call_args = mock_webpush.call_args
            import json
            payload = json.loads(call_args[1]["data"])
            assert payload["title"] == "Review Lyrics"
            assert "Test Song" in payload["body"]
            assert "Test Artist" in payload["body"]
            assert "/review/test-job-123" in payload["url"]

    @pytest.mark.asyncio
    async def test_send_blocking_notification_instrumental(self, push_service):
        """send_blocking_notification formats instrumental selection notification."""
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "push_subscriptions": [{
                "endpoint": "https://push.example.com/endpoint",
                "keys": {"p256dh": "key", "auth": "auth"}
            }]
        }
        push_service.db.collection.return_value.document.return_value.get.return_value = mock_doc

        job = {
            "job_id": "test-job-123",
            "user_email": "test@example.com",
            "artist": "Test Artist",
            "title": "Test Song"
        }

        with patch('backend.services.push_notification_service.webpush') as mock_webpush:
            await push_service.send_blocking_notification(job, "instrumental")

            call_args = mock_webpush.call_args
            import json
            payload = json.loads(call_args[1]["data"])
            assert payload["title"] == "Select Instrumental"
            assert "/instrumental/test-job-123" in payload["url"]

    @pytest.mark.asyncio
    async def test_send_completion_notification(self, push_service):
        """send_completion_notification formats completion notification."""
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "push_subscriptions": [{
                "endpoint": "https://push.example.com/endpoint",
                "keys": {"p256dh": "key", "auth": "auth"}
            }]
        }
        push_service.db.collection.return_value.document.return_value.get.return_value = mock_doc

        job = {
            "job_id": "test-job-123",
            "user_email": "test@example.com",
            "artist": "Test Artist",
            "title": "Test Song"
        }

        with patch('backend.services.push_notification_service.webpush') as mock_webpush:
            await push_service.send_completion_notification(job)

            call_args = mock_webpush.call_args
            import json
            payload = json.loads(call_args[1]["data"])
            assert payload["title"] == "Video Ready!"
            assert "Test Song" in payload["body"]
            assert "Test Artist" in payload["body"]
            assert "download" in payload["body"].lower()


class TestSingleton:
    """Tests for singleton pattern."""

    def test_get_push_notification_service_returns_singleton(self):
        """get_push_notification_service returns same instance."""
        # Reset singleton for test
        import backend.services.push_notification_service as module
        module._push_service = None

        with patch('backend.services.push_notification_service.get_settings') as mock_get_settings:
            mock_settings = Mock()
            mock_settings.enable_push_notifications = False
            mock_settings.get_secret = Mock(return_value=None)
            mock_get_settings.return_value = mock_settings

            service1 = get_push_notification_service()
            service2 = get_push_notification_service()

            assert service1 is service2

        # Clean up
        module._push_service = None
