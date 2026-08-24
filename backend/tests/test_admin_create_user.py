"""
Unit tests for the admin create-user endpoint.

Tests POST /api/users/admin/users, which lets admins create a user account
directly (e.g. to grant credits, submit jobs on behalf of, or impersonate
someone who has never logged in).
"""
import pytest
from unittest.mock import Mock
from fastapi.testclient import TestClient
from fastapi import FastAPI

from backend.api.routes.users import router
from backend.api.dependencies import require_admin
from backend.services.user_service import get_user_service
from backend.services.auth_service import AuthResult, UserType
from backend.models.user import User


app = FastAPI()
app.include_router(router, prefix="/api")


def get_mock_admin():
    """Override for require_admin dependency."""
    return AuthResult(
        is_valid=True,
        user_type=UserType.ADMIN,
        remaining_uses=-1,
        message="Admin session valid",
        user_email="admin@nomadkaraoke.com",
        is_admin=True,
    )


@pytest.fixture
def mock_user_service():
    service = Mock()
    service.get_user.return_value = None  # default: user doesn't exist
    new_user = Mock(spec=User)
    new_user.email = "new@example.com"
    new_user.credits = 0
    service.get_or_create_user.return_value = new_user
    service.add_credits.return_value = (True, 3, "Added 3 credits")
    return service


@pytest.fixture
def client(mock_user_service):
    app.dependency_overrides[require_admin] = get_mock_admin
    app.dependency_overrides[get_user_service] = lambda: mock_user_service
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestAdminCreateUser:
    """Tests for POST /api/users/admin/users."""

    def test_create_user_minimal(self, client, mock_user_service):
        """Creating with just an email creates the account with zero credits."""
        response = client.post(
            "/api/users/admin/users",
            json={"email": "new@example.com"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "success"
        assert data["email"] == "new@example.com"
        assert data["credits"] == 0
        mock_user_service.get_or_create_user.assert_called_once_with("new@example.com")
        mock_user_service.add_credits.assert_not_called()
        mock_user_service.update_user.assert_not_called()

    def test_create_user_with_credits_and_display_name(self, client, mock_user_service):
        """Initial credits are granted and the welcome-credit flag is set."""
        response = client.post(
            "/api/users/admin/users",
            json={
                "email": "new@example.com",
                "display_name": "Uwe Schreiber",
                "initial_credits": 3,
                "credit_reason": "made-for-you order",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["credits"] == 3
        assert "3 credit(s)" in data["message"]

        mock_user_service.add_credits.assert_called_once_with(
            email="new@example.com",
            amount=3,
            reason="made-for-you order",
            admin_email="admin@nomadkaraoke.com",
        )
        # display_name saved + welcome credit suppressed (admin credits replace it)
        update_kwargs = mock_user_service.update_user.call_args.kwargs
        assert update_kwargs["display_name"] == "Uwe Schreiber"
        assert update_kwargs["welcome_credits_granted"] is True

    def test_create_without_credits_leaves_welcome_credit_eligible(self, client, mock_user_service):
        """No initial credits → welcome_credits_granted is left untouched."""
        response = client.post(
            "/api/users/admin/users",
            json={"email": "new@example.com", "display_name": "Somebody"},
        )

        assert response.status_code == 201
        update_kwargs = mock_user_service.update_user.call_args.kwargs
        assert "welcome_credits_granted" not in update_kwargs

    def test_email_normalized_to_lowercase(self, client, mock_user_service):
        """Mixed-case emails are lowercased before lookup and creation."""
        response = client.post(
            "/api/users/admin/users",
            json={"email": "Schreiber.Uwe@GoogleMail.com"},
        )

        assert response.status_code == 201
        assert response.json()["email"] == "schreiber.uwe@googlemail.com"
        mock_user_service.get_user.assert_called_once_with("schreiber.uwe@googlemail.com")
        mock_user_service.get_or_create_user.assert_called_once_with("schreiber.uwe@googlemail.com")

    def test_duplicate_user_returns_409(self, client, mock_user_service):
        """Creating a user that already exists returns 409."""
        mock_user_service.get_user.return_value = Mock(spec=User)

        response = client.post(
            "/api/users/admin/users",
            json={"email": "existing@example.com"},
        )

        assert response.status_code == 409
        assert "already exists" in response.json()["detail"]
        mock_user_service.get_or_create_user.assert_not_called()

    def test_invalid_email_rejected(self, client, mock_user_service):
        """A malformed email fails validation with 422."""
        response = client.post(
            "/api/users/admin/users",
            json={"email": "not-an-email"},
        )

        assert response.status_code == 422
        mock_user_service.get_or_create_user.assert_not_called()

    def test_credits_out_of_range_rejected(self, client, mock_user_service):
        """initial_credits outside 0-1000 is rejected."""
        for bad_amount in (-1, 1001):
            response = client.post(
                "/api/users/admin/users",
                json={"email": "new@example.com", "initial_credits": bad_amount},
            )
            assert response.status_code == 400
        mock_user_service.get_or_create_user.assert_not_called()

    def test_add_credits_failure_returns_500(self, client, mock_user_service):
        """If granting credits fails after creation, the endpoint reports it."""
        mock_user_service.add_credits.return_value = (False, 0, "Firestore unavailable")

        response = client.post(
            "/api/users/admin/users",
            json={"email": "new@example.com", "initial_credits": 2},
        )

        assert response.status_code == 500
        assert "adding credits failed" in response.json()["detail"]

    def test_requires_admin(self, mock_user_service):
        """Non-admin requests are rejected."""
        def deny_admin():
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="Admin access required")

        app.dependency_overrides[require_admin] = deny_admin
        app.dependency_overrides[get_user_service] = lambda: mock_user_service
        try:
            client = TestClient(app)
            response = client.post(
                "/api/users/admin/users",
                json={"email": "new@example.com"},
            )
            assert response.status_code == 403
        finally:
            app.dependency_overrides.clear()
