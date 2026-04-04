"""
Tests for Referral API routes.

Tests the /api/referrals/* endpoints for referral link management,
interstitial pages, dashboard, and admin operations.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from fastapi.testclient import TestClient

from backend.main import app
from backend.api.dependencies import require_auth, require_admin
from backend.services.auth_service import UserType, AuthResult
from backend.models.referral import ReferralLink, ReferralLinkStats


@pytest.fixture(autouse=True)
def _clear_overrides():
    """Clear dependency overrides before and after each test."""
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


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


def _make_referral_link(**overrides) -> ReferralLink:
    """Helper to create a ReferralLink with sensible defaults."""
    defaults = {
        "code": "abc12345",
        "owner_email": "referrer@example.com",
        "display_name": "Test Referrer",
        "custom_message": "Join karaoke!",
        "discount_percent": 10,
        "kickback_percent": 20,
        "discount_duration_days": 30,
        "earning_duration_days": 365,
        "is_vanity": False,
        "enabled": True,
        "stats": ReferralLinkStats(clicks=5, signups=2, purchases=1, total_earned_cents=500),
    }
    defaults.update(overrides)
    return ReferralLink(**defaults)


# ============================================================================
# GET /api/referrals/r/{code} - Public interstitial
# ============================================================================


class TestGetInterstitial:
    """Tests for GET /api/referrals/r/{code}."""

    @patch("backend.api.routes.referrals.get_referral_service")
    def test_valid_code_returns_interstitial(self, mock_get_service, client):
        """Valid code returns 200 with referral data and display_name."""
        mock_service = Mock()
        link = _make_referral_link()
        mock_service.get_link_by_code.return_value = link
        mock_get_service.return_value = mock_service

        response = client.get("/api/referrals/r/abc12345")

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert data["referral_code"] == "abc12345"
        assert data["referrer_display_name"] == "Test Referrer"
        assert data["discount_percent"] == 10
        assert data["discount_duration_days"] == 30
        assert data["custom_message"] == "Join karaoke!"
        mock_service.increment_clicks.assert_called_once_with("abc12345")

    @patch("backend.api.routes.referrals.get_referral_service")
    def test_invalid_code_returns_valid_false(self, mock_get_service, client):
        """Invalid code returns 200 with valid=False."""
        mock_service = Mock()
        mock_service.get_link_by_code.return_value = None
        mock_get_service.return_value = mock_service

        response = client.get("/api/referrals/r/nonexistent")

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        mock_service.increment_clicks.assert_not_called()

    @patch("backend.api.routes.referrals.get_referral_service")
    def test_click_increment_failure_does_not_break_response(self, mock_get_service, client):
        """Click tracking failure should not affect the response."""
        mock_service = Mock()
        link = _make_referral_link()
        mock_service.get_link_by_code.return_value = link
        mock_service.increment_clicks.side_effect = Exception("Firestore error")
        mock_get_service.return_value = mock_service

        response = client.get("/api/referrals/r/abc12345")

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True


# ============================================================================
# GET /api/referrals/me - Dashboard
# ============================================================================


class TestGetDashboard:
    """Tests for GET /api/referrals/me."""

    @patch("backend.api.routes.referrals.get_referral_service")
    def test_authenticated_user_gets_dashboard(self, mock_get_service, client, mock_auth_result):
        """Authenticated user gets their referral dashboard."""
        app.dependency_overrides[require_auth] = lambda: mock_auth_result

        mock_service = Mock()
        mock_service.get_dashboard_data.return_value = {
            "link": {"code": "abc12345", "display_name": "Test"},
            "pending_balance_cents": 500,
            "total_earned_cents": 1000,
            "total_paid_cents": 200,
            "recent_earnings": [],
            "recent_payouts": [],
            "stripe_connect_configured": False,
        }
        mock_get_service.return_value = mock_service

        response = client.get("/api/referrals/me")

        assert response.status_code == 200
        data = response.json()
        assert data["link"]["code"] == "abc12345"
        assert data["pending_balance_cents"] == 500
        assert data["stripe_connect_configured"] is False
        mock_service.get_dashboard_data.assert_called_once_with("test@example.com")

    def test_unauthenticated_returns_401(self, client):
        """Unauthenticated request returns 401."""
        # No dependency override -- real auth will reject
        response = client.get("/api/referrals/me")
        assert response.status_code == 401


# ============================================================================
# PUT /api/referrals/me - Update own link
# ============================================================================


class TestUpdateMyLink:
    """Tests for PUT /api/referrals/me."""

    @patch("backend.api.routes.referrals.get_referral_service")
    def test_update_display_name(self, mock_get_service, client, mock_auth_result):
        """User can update their display name."""
        app.dependency_overrides[require_auth] = lambda: mock_auth_result

        mock_service = Mock()
        link = _make_referral_link(owner_email="test@example.com")
        mock_service.get_or_create_link.return_value = link
        mock_service.update_link.return_value = (True, "Link updated")
        mock_get_service.return_value = mock_service

        response = client.put(
            "/api/referrals/me",
            json={"display_name": "New Name"},
        )

        assert response.status_code == 200
        assert response.json()["ok"] is True
        mock_service.update_link.assert_called_once_with(
            "abc12345", display_name="New Name"
        )


# ============================================================================
# POST /api/referrals/me/connect - Stripe Connect
# ============================================================================


class TestStripeConnect:
    """Tests for POST /api/referrals/me/connect."""

    @patch("backend.services.user_service.get_user_service")
    @patch("backend.api.routes.referrals.get_referral_service")
    def test_start_connect_onboarding(self, mock_get_service, mock_get_user_svc, client, mock_auth_result):
        """User can start Stripe Connect onboarding."""
        app.dependency_overrides[require_auth] = lambda: mock_auth_result

        mock_service = Mock()
        mock_service.create_connect_account.return_value = ("acct_123", "https://connect.stripe.com/onboard")
        mock_get_service.return_value = mock_service

        # Mock user without existing Connect account
        mock_user = Mock()
        mock_user.stripe_connect_account_id = None
        mock_user_service = Mock()
        mock_user_service.get_user.return_value = mock_user
        mock_get_user_svc.return_value = mock_user_service

        response = client.post("/api/referrals/me/connect")

        assert response.status_code == 200
        data = response.json()
        assert data["account_id"] == "acct_123"
        assert "onboarding_url" in data


# ============================================================================
# Admin endpoints
# ============================================================================


class TestAdminVanityLink:
    """Tests for POST /api/referrals/admin/vanity."""

    @patch("backend.api.routes.referrals.get_referral_service")
    def test_create_vanity_link(self, mock_get_service, client, mock_admin_auth_result):
        """Admin can create a vanity referral link."""
        app.dependency_overrides[require_admin] = lambda: mock_admin_auth_result

        mock_service = Mock()
        link = _make_referral_link(code="karaoke-king", is_vanity=True)
        mock_service.create_vanity_link.return_value = (True, link, "Vanity link created")
        mock_get_service.return_value = mock_service

        response = client.post(
            "/api/referrals/admin/vanity",
            json={"vanity_code": "karaoke-king", "owner_email": "king@example.com", "display_name": "King of Karaoke"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["code"] == "karaoke-king"

    @patch("backend.api.routes.referrals.get_referral_service")
    def test_vanity_code_required(self, mock_get_service, client, mock_admin_auth_result):
        """Vanity code is required for vanity link creation."""
        app.dependency_overrides[require_admin] = lambda: mock_admin_auth_result

        response = client.post(
            "/api/referrals/admin/vanity",
            json={"owner_email": "user@example.com", "display_name": "No Code"},
        )

        assert response.status_code == 400


class TestAdminListLinks:
    """Tests for GET /api/referrals/admin/links."""

    @patch("backend.api.routes.referrals.get_referral_service")
    def test_list_links(self, mock_get_service, client, mock_admin_auth_result):
        """Admin can list all referral links."""
        app.dependency_overrides[require_admin] = lambda: mock_admin_auth_result

        mock_service = Mock()
        mock_service.list_links.return_value = [
            _make_referral_link(code="link1"),
            _make_referral_link(code="link2"),
        ]
        mock_get_service.return_value = mock_service

        response = client.get("/api/referrals/admin/links")

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2
        assert len(data["links"]) == 2

    def test_non_admin_returns_403(self, client, mock_auth_result):
        """Non-admin user gets 403."""
        app.dependency_overrides[require_auth] = lambda: mock_auth_result
        # Don't override require_admin -- it will check is_admin=False and raise 403

        response = client.get("/api/referrals/admin/links")
        assert response.status_code == 403


class TestAdminUpdateLink:
    """Tests for PUT /api/referrals/admin/links/{code}."""

    @patch("backend.api.routes.referrals.get_referral_service")
    def test_admin_update_link(self, mock_get_service, client, mock_admin_auth_result):
        """Admin can update any link."""
        app.dependency_overrides[require_admin] = lambda: mock_admin_auth_result

        mock_service = Mock()
        mock_service.update_link.return_value = (True, "Link updated")
        mock_get_service.return_value = mock_service

        response = client.put(
            "/api/referrals/admin/links/abc12345",
            json={"enabled": False},
        )

        assert response.status_code == 200
        assert response.json()["ok"] is True
        mock_service.update_link.assert_called_once_with("abc12345", enabled=False)
