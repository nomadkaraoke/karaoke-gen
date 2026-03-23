"""
Tests for anti-abuse improvements: credits on verification, IP rate limiting,
and device fingerprinting.

Covers:
- Phase 1: Welcome credits granted on verification, not account creation
- Phase 2: Per-IP signup rate limiting (2 new accounts per 24h)
- Phase 3: Device fingerprint rate limiting
"""
import pytest
import uuid
from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock, patch
from fastapi.testclient import TestClient

# Mock Firestore before importing modules that use it
import sys
sys.modules.setdefault('google.cloud.firestore', MagicMock())
sys.modules.setdefault('google.cloud.firestore_v1', MagicMock())

from backend.models.user import (
    User,
    MagicLinkToken,
    CreditTransaction,
    SendMagicLinkRequest,
)


# =============================================================================
# Phase 1: Credits on Verification
# =============================================================================


class TestGetOrCreateUserNoCredits:
    """New users should be created with 0 credits."""

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_new_user_created_with_zero_credits(self, mock_fs, mock_settings):
        """get_or_create_user creates users with 0 credits."""
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        # Simulate user not found
        mock_doc = MagicMock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc
        mock_db.collection.return_value.where.return_value.limit.return_value.stream.return_value = []

        from backend.services.user_service import UserService
        service = UserService()
        user = service.get_or_create_user("new@example.com")

        assert user.credits == 0
        assert user.credit_transactions == []

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_new_user_stores_signup_ip(self, mock_fs, mock_settings):
        """get_or_create_user stores the signup IP."""
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        mock_doc = MagicMock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc
        mock_db.collection.return_value.where.return_value.limit.return_value.stream.return_value = []

        from backend.services.user_service import UserService
        service = UserService()
        user = service.get_or_create_user(
            "new@example.com", signup_ip="1.2.3.4"
        )

        assert user.signup_ip == "1.2.3.4"

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_new_user_stores_device_fingerprint(self, mock_fs, mock_settings):
        """get_or_create_user stores the device fingerprint."""
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        mock_doc = MagicMock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc
        mock_db.collection.return_value.where.return_value.limit.return_value.stream.return_value = []

        from backend.services.user_service import UserService
        service = UserService()
        user = service.get_or_create_user(
            "new@example.com", device_fingerprint="abc123"
        )

        assert user.device_fingerprint == "abc123"


class TestGrantWelcomeCredits:
    """Welcome credits should be granted on first verification."""

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_grants_credits_to_new_user(self, mock_fs, mock_settings):
        """grant_welcome_credits_if_eligible grants credits to user without welcome_credit txn."""
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        # User exists with 0 credits and no transactions
        user = User(email="new@example.com", credits=0, credit_transactions=[])
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = user.model_dump(mode='json')
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        from backend.services.user_service import UserService
        service = UserService()
        with patch('backend.services.credit_evaluation_service.get_credit_evaluation_service') as mock_eval:
            from backend.services.credit_evaluation_service import CreditEvaluation
            mock_eval.return_value.evaluate.return_value = CreditEvaluation(decision="grant", reasoning="OK", confidence=1.0)
            granted, status = service.grant_welcome_credits_if_eligible("new@example.com")

        assert granted is True
        # Verify update was called with credits and flag
        mock_db.collection.return_value.document.return_value.update.assert_called_once()
        update_args = mock_db.collection.return_value.document.return_value.update.call_args[0][0]
        assert update_args['credits'] == 2
        assert update_args['welcome_credits_granted'] is True

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_skips_if_flag_already_set(self, mock_fs, mock_settings):
        """grant_welcome_credits_if_eligible returns False if welcome_credits_granted flag is True."""
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        # User has the flag set (even if transactions were trimmed)
        user = User(
            email="existing@example.com",
            credits=2,
            welcome_credits_granted=True,
        )
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = user.model_dump(mode='json')
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        from backend.services.user_service import UserService
        service = UserService()
        granted, status = service.grant_welcome_credits_if_eligible("existing@example.com")

        assert granted is False

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_skips_if_transaction_exists(self, mock_fs, mock_settings):
        """grant_welcome_credits_if_eligible returns False if welcome_credit txn exists (secondary check)."""
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        # User has transaction but flag not set (legacy user)
        user = User(
            email="existing@example.com",
            credits=2,
            welcome_credits_granted=False,
            credit_transactions=[
                CreditTransaction(
                    id=str(uuid.uuid4()),
                    amount=2,
                    reason="welcome_credit",
                )
            ],
        )
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = user.model_dump(mode='json')
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        from backend.services.user_service import UserService
        service = UserService()
        granted, status = service.grant_welcome_credits_if_eligible("existing@example.com")

        assert granted is False

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_returns_false_for_nonexistent_user(self, mock_fs, mock_settings):
        """grant_welcome_credits_if_eligible returns False for unknown user."""
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        mock_doc = MagicMock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc
        mock_db.collection.return_value.where.return_value.limit.return_value.stream.return_value = []

        from backend.services.user_service import UserService
        service = UserService()
        granted, status = service.grant_welcome_credits_if_eligible("ghost@example.com")

        assert granted is False


# =============================================================================
# Phase 2: Per-IP Signup Rate Limiting
# =============================================================================


class TestIPRateLimiting:
    """Test IP-based signup rate limiting."""

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_count_recent_signups_from_ip(self, mock_fs, mock_settings):
        """count_recent_signups_from_ip counts matching users."""
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        # Simulate 2 matching users
        mock_docs = [MagicMock(), MagicMock()]
        mock_db.collection.return_value.where.return_value.where.return_value.stream.return_value = mock_docs

        from backend.services.user_service import UserService
        service = UserService()
        count = service.count_recent_signups_from_ip("1.2.3.4")

        assert count == 2

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_is_signup_rate_limited_by_ip(self, mock_fs, mock_settings):
        """is_signup_rate_limited returns True when IP count >= 2."""
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        # 2 signups = rate limited
        mock_docs = [MagicMock(), MagicMock()]
        mock_db.collection.return_value.where.return_value.where.return_value.stream.return_value = mock_docs

        from backend.services.user_service import UserService
        service = UserService()
        assert service.is_signup_rate_limited(ip_address="1.2.3.4") is True

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_not_rate_limited_with_one_signup(self, mock_fs, mock_settings):
        """is_signup_rate_limited returns False when IP count < 2."""
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        mock_docs = [MagicMock()]
        mock_db.collection.return_value.where.return_value.where.return_value.stream.return_value = mock_docs

        from backend.services.user_service import UserService
        service = UserService()
        assert service.is_signup_rate_limited(ip_address="1.2.3.4") is False

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_no_ip_not_rate_limited(self, mock_fs, mock_settings):
        """is_signup_rate_limited returns False when no IP provided."""
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        from backend.services.user_service import UserService
        service = UserService()
        assert service.is_signup_rate_limited(ip_address=None) is False


# =============================================================================
# Phase 3: Device Fingerprint Rate Limiting
# =============================================================================


class TestFingerprintRateLimiting:
    """Test fingerprint-based signup rate limiting."""

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_count_recent_signups_from_fingerprint(self, mock_fs, mock_settings):
        """count_recent_signups_from_fingerprint counts matching users."""
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        mock_docs = [MagicMock(), MagicMock(), MagicMock()]
        mock_db.collection.return_value.where.return_value.where.return_value.stream.return_value = mock_docs

        from backend.services.user_service import UserService
        service = UserService()
        count = service.count_recent_signups_from_fingerprint("fp123")

        assert count == 3

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_is_signup_rate_limited_by_fingerprint(self, mock_fs, mock_settings):
        """is_signup_rate_limited returns True when fingerprint count >= 2."""
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        # IP returns 0, fingerprint returns 2
        def side_effect_stream():
            return iter([MagicMock(), MagicMock()])

        # We need separate mock chains for IP and fingerprint queries
        # Since both use the same mock chain, we'll set up the calls differently
        call_count = [0]
        def stream_side_effect():
            call_count[0] += 1
            if call_count[0] == 1:
                return iter([])  # IP query: 0 results
            return iter([MagicMock(), MagicMock()])  # Fingerprint query: 2 results

        mock_db.collection.return_value.where.return_value.where.return_value.stream = stream_side_effect

        from backend.services.user_service import UserService
        service = UserService()
        result = service.is_signup_rate_limited(
            ip_address="1.2.3.4", device_fingerprint="fp123"
        )

        assert result is True

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_no_fingerprint_not_rate_limited(self, mock_fs, mock_settings):
        """is_signup_rate_limited doesn't check fingerprint when None."""
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        # IP query returns 0
        mock_db.collection.return_value.where.return_value.where.return_value.stream.return_value = []

        from backend.services.user_service import UserService
        service = UserService()
        result = service.is_signup_rate_limited(
            ip_address="1.2.3.4", device_fingerprint=None
        )

        assert result is False


# =============================================================================
# Integration: Magic Link Endpoint Rate Limiting
# =============================================================================


@pytest.fixture
def mock_validation_svc():
    """Create a mock EmailValidationService."""
    svc = MagicMock()
    svc.is_disposable_domain.return_value = False
    svc.is_email_blocked.return_value = False
    svc.is_ip_blocked.return_value = False
    return svc


@pytest.fixture
def mock_user_svc():
    """Create a mock UserService."""
    svc = MagicMock()
    svc.get_user.return_value = None  # New user by default
    svc.is_signup_rate_limited.return_value = False
    svc.create_magic_link.return_value = MagicLinkToken(
        token="test-token",
        email="test@example.com",
        expires_at=datetime.utcnow() + timedelta(minutes=15),
    )
    svc.NEW_USER_FREE_CREDITS = 2
    return svc


@pytest.fixture
def mock_email_svc():
    """Create a mock EmailService."""
    svc = MagicMock()
    svc.is_configured.return_value = True
    svc.send_magic_link.return_value = True
    return svc


@pytest.fixture
def client(mock_validation_svc, mock_user_svc, mock_email_svc):
    """Create TestClient with mocked services via FastAPI dependency overrides."""
    from backend.main import app
    from backend.services.user_service import get_user_service
    from backend.services.email_service import get_email_service

    app.dependency_overrides[get_user_service] = lambda: mock_user_svc
    app.dependency_overrides[get_email_service] = lambda: mock_email_svc

    with patch(
        "backend.api.routes.users.get_email_validation_service",
        return_value=mock_validation_svc,
    ):
        yield TestClient(app)

    app.dependency_overrides.clear()


class TestMagicLinkRateLimiting:
    """Integration tests for rate limiting in the magic link endpoint."""

    def test_new_signup_allowed_when_not_rate_limited(
        self, client, mock_user_svc
    ):
        """New signup succeeds when not rate limited."""
        mock_user_svc.get_user.return_value = None
        mock_user_svc.is_signup_rate_limited.return_value = False

        response = client.post(
            "/api/users/auth/magic-link",
            json={"email": "new@example.com"},
        )

        assert response.status_code == 200
        mock_user_svc.create_magic_link.assert_called_once()

    def test_new_signup_blocked_when_rate_limited(
        self, client, mock_user_svc
    ):
        """New signup is silently rejected when rate limited."""
        mock_user_svc.get_user.return_value = None
        mock_user_svc.is_signup_rate_limited.return_value = True

        response = client.post(
            "/api/users/auth/magic-link",
            json={"email": "abuser@example.com"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "success"
        # Magic link should NOT have been created
        mock_user_svc.create_magic_link.assert_not_called()

    def test_existing_user_not_rate_limited(
        self, client, mock_user_svc
    ):
        """Returning user bypasses rate limiting."""
        mock_user_svc.get_user.return_value = User(
            email="existing@example.com", credits=5
        )

        response = client.post(
            "/api/users/auth/magic-link",
            json={"email": "existing@example.com"},
        )

        assert response.status_code == 200
        # Rate limit check should not be called for existing users
        mock_user_svc.is_signup_rate_limited.assert_not_called()
        mock_user_svc.create_magic_link.assert_called_once()

    def test_fingerprint_passed_to_create_magic_link(
        self, client, mock_user_svc
    ):
        """Device fingerprint from request body is passed to create_magic_link."""
        mock_user_svc.get_user.return_value = None
        mock_user_svc.is_signup_rate_limited.return_value = False

        response = client.post(
            "/api/users/auth/magic-link",
            json={"email": "new@example.com", "device_fingerprint": "fp-abc123"},
        )

        assert response.status_code == 200
        create_call = mock_user_svc.create_magic_link.call_args
        assert create_call.kwargs.get('device_fingerprint') == "fp-abc123"

    def test_fingerprint_passed_to_rate_limiter(
        self, client, mock_user_svc
    ):
        """Device fingerprint is included in rate limit check."""
        mock_user_svc.get_user.return_value = None
        mock_user_svc.is_signup_rate_limited.return_value = False

        response = client.post(
            "/api/users/auth/magic-link",
            json={"email": "new@example.com", "device_fingerprint": "fp-abc123"},
        )

        rate_limit_call = mock_user_svc.is_signup_rate_limited.call_args
        assert rate_limit_call.kwargs.get('device_fingerprint') == "fp-abc123"

    def test_missing_fingerprint_gracefully_handled(
        self, client, mock_user_svc
    ):
        """Missing fingerprint doesn't break the flow."""
        mock_user_svc.get_user.return_value = None
        mock_user_svc.is_signup_rate_limited.return_value = False

        response = client.post(
            "/api/users/auth/magic-link",
            json={"email": "new@example.com"},
        )

        assert response.status_code == 200
        rate_limit_call = mock_user_svc.is_signup_rate_limited.call_args
        assert rate_limit_call.kwargs.get('device_fingerprint') is None


# =============================================================================
# Integration: Verify Endpoint Grants Credits
# =============================================================================


class TestVerifyGrantsCredits:
    """Test that the verify endpoint grants welcome credits."""

    def _make_verify_client(self, mock_user_svc, user, credits_eligible):
        """Helper to create a TestClient for verify endpoint tests."""
        from backend.main import app
        from backend.services.user_service import get_user_service
        from backend.services.email_service import get_email_service

        mock_user_svc.verify_magic_link.return_value = (True, user, "Success")
        mock_user_svc.grant_welcome_credits_if_eligible.return_value = (credits_eligible, "granted" if credits_eligible else "already_granted")
        mock_user_svc.get_user.return_value = user
        mock_user_svc.create_session.return_value = MagicMock(token="session-token")
        mock_user_svc.NEW_USER_FREE_CREDITS = 2

        # Mock magic link doc for first-login check
        mock_ml_doc = MagicMock()
        mock_ml_doc.exists = True
        mock_ml_doc.to_dict.return_value = {
            'email': user.email,
            'tenant_id': None,
        }
        mock_user_svc.db.collection.return_value.document.return_value.get.return_value = mock_ml_doc

        mock_email_svc = MagicMock()

        app.dependency_overrides[get_user_service] = lambda: mock_user_svc
        app.dependency_overrides[get_email_service] = lambda: mock_email_svc

        client = TestClient(app)
        return client

    def test_verify_returns_credits_granted(self, mock_user_svc):
        """Verify endpoint returns credits_granted in response."""
        user = User(email="new@example.com", credits=2, email_verified=True)
        client = self._make_verify_client(mock_user_svc, user, credits_eligible=True)

        try:
            response = client.get("/api/users/auth/verify?token=valid-token")

            assert response.status_code == 200
            data = response.json()
            assert data["credits_granted"] == 2
            mock_user_svc.grant_welcome_credits_if_eligible.assert_called_once()
        finally:
            from backend.main import app
            app.dependency_overrides.clear()

    def test_verify_returns_zero_credits_for_returning_user(self, mock_user_svc):
        """Returning user gets credits_granted=0."""
        user = User(
            email="existing@example.com",
            credits=5,
            email_verified=True,
            last_login_at=datetime.utcnow(),
        )
        client = self._make_verify_client(mock_user_svc, user, credits_eligible=False)

        try:
            response = client.get("/api/users/auth/verify?token=valid-token")

            assert response.status_code == 200
            data = response.json()
            assert data["credits_granted"] == 0
        finally:
            from backend.main import app
            app.dependency_overrides.clear()


# =============================================================================
# Model Tests
# =============================================================================


class TestModelFields:
    """Test that new fields exist on models."""

    def test_user_has_signup_ip(self):
        """User model has signup_ip field."""
        user = User(email="test@example.com", signup_ip="1.2.3.4")
        assert user.signup_ip == "1.2.3.4"

    def test_user_has_device_fingerprint(self):
        """User model has device_fingerprint field."""
        user = User(email="test@example.com", device_fingerprint="fp123")
        assert user.device_fingerprint == "fp123"

    def test_user_defaults_none(self):
        """New fields default to None."""
        user = User(email="test@example.com")
        assert user.signup_ip is None
        assert user.device_fingerprint is None

    def test_magic_link_has_device_fingerprint(self):
        """MagicLinkToken model has device_fingerprint field."""
        ml = MagicLinkToken(
            token="tok",
            email="test@example.com",
            expires_at=datetime.utcnow(),
            device_fingerprint="fp123",
        )
        assert ml.device_fingerprint == "fp123"

    def test_send_magic_link_request_has_fingerprint(self):
        """SendMagicLinkRequest accepts device_fingerprint."""
        req = SendMagicLinkRequest(
            email="test@example.com",
            device_fingerprint="fp123",
        )
        assert req.device_fingerprint == "fp123"

    def test_send_magic_link_request_fingerprint_optional(self):
        """SendMagicLinkRequest works without device_fingerprint."""
        req = SendMagicLinkRequest(email="test@example.com")
        assert req.device_fingerprint is None


# =============================================================================
# Error Path Tests
# =============================================================================


class TestErrorPaths:
    """Test graceful degradation on errors."""

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_grant_credits_handles_exception(self, mock_fs, mock_settings):
        """grant_welcome_credits_if_eligible returns False on exception."""
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        # User lookup raises exception
        mock_db.collection.return_value.document.return_value.get.side_effect = Exception("Firestore down")

        from backend.services.user_service import UserService
        service = UserService()
        granted, status = service.grant_welcome_credits_if_eligible("test@example.com")
        assert granted is False

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_count_signups_from_ip_handles_exception(self, mock_fs, mock_settings):
        """count_recent_signups_from_ip returns 0 on Firestore error (fail-open)."""
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        mock_db.collection.return_value.where.return_value.where.side_effect = Exception("Query error")

        from backend.services.user_service import UserService
        service = UserService()
        count = service.count_recent_signups_from_ip("1.2.3.4")
        assert count == 0  # Fail-open: don't block on error

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_count_signups_from_fingerprint_handles_exception(self, mock_fs, mock_settings):
        """count_recent_signups_from_fingerprint returns 0 on error (fail-open)."""
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        mock_db.collection.return_value.where.return_value.where.side_effect = Exception("Query error")

        from backend.services.user_service import UserService
        service = UserService()
        count = service.count_recent_signups_from_fingerprint("fp123")
        assert count == 0


# =============================================================================
# Caller-Callee Contract Tests
# =============================================================================


class TestCallerCalleeContracts:
    """Verify that callers pass the right data to callees."""

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_create_magic_link_passes_fingerprint_to_get_or_create(self, mock_fs, mock_settings):
        """create_magic_link passes device_fingerprint through to get_or_create_user."""
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        # User already exists (to avoid save path)
        user = User(email="test@example.com", credits=0)
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = user.model_dump(mode='json')
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        from backend.services.user_service import UserService
        service = UserService()

        with patch.object(service, 'get_or_create_user', wraps=service.get_or_create_user) as spy:
            service.create_magic_link(
                "test@example.com",
                ip_address="1.2.3.4",
                device_fingerprint="fp-xyz",
            )
            spy.assert_called_once_with(
                "test@example.com",
                tenant_id=None,
                signup_ip="1.2.3.4",
                device_fingerprint="fp-xyz",
            )

    @patch('backend.services.user_service.get_settings')
    @patch('backend.services.user_service.firestore')
    def test_create_magic_link_stores_fingerprint_on_token(self, mock_fs, mock_settings):
        """create_magic_link includes device_fingerprint in the MagicLinkToken."""
        mock_settings.return_value = MagicMock(google_cloud_project='test')
        mock_db = MagicMock()
        mock_fs.Client.return_value = mock_db

        user = User(email="test@example.com", credits=0)
        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = user.model_dump(mode='json')
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        from backend.services.user_service import UserService
        service = UserService()

        ml = service.create_magic_link(
            "test@example.com",
            ip_address="1.2.3.4",
            device_fingerprint="fp-xyz",
        )

        assert ml.device_fingerprint == "fp-xyz"

        # Also verify the data written to Firestore includes the fingerprint
        set_call = mock_db.collection.return_value.document.return_value.set
        set_call.assert_called()
        saved_data = set_call.call_args[0][0]
        assert saved_data["device_fingerprint"] == "fp-xyz"
