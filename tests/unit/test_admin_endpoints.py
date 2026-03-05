"""Tests for admin endpoints in backend/api/routes/users.py.

Verifies that admin endpoints correctly use AuthResult from require_admin
dependency (not legacy tuple unpacking).
"""
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient

from backend.services.auth_service import AuthResult, UserType


@pytest.fixture
def admin_auth_result():
    """AuthResult as returned by require_admin for admin token auth."""
    return AuthResult(
        is_valid=True,
        user_type=UserType.ADMIN,
        remaining_uses=-1,
        message="Admin access granted",
        user_email="admin@nomadkaraoke.com",
        is_admin=True,
    )


@pytest.fixture
def mock_user_service():
    service = MagicMock()
    service.add_credits.return_value = (True, 12, "Added 2 credits")
    service.disable_user.return_value = True
    service.enable_user.return_value = True
    service.delete_user.return_value = True
    service.set_user_role.return_value = True
    return service


@pytest.fixture
def mock_email_service():
    service = MagicMock()
    service.send_credits_added.return_value = True
    return service


@pytest.fixture
def client(admin_auth_result, mock_user_service, mock_email_service):
    """Test client with mocked dependencies."""
    from fastapi import FastAPI
    from backend.api.routes.users import router
    from backend.api.dependencies import require_admin
    from backend.services.user_service import get_user_service
    from backend.services.email_service import get_email_service

    app = FastAPI()
    app.include_router(router, prefix="/api")

    app.dependency_overrides[require_admin] = lambda: admin_auth_result
    app.dependency_overrides[get_user_service] = lambda: mock_user_service
    app.dependency_overrides[get_email_service] = lambda: mock_email_service

    return TestClient(app)


class TestAddCredits:
    def test_add_credits_success(self, client, mock_user_service):
        response = client.post(
            "/api/users/admin/credits",
            json={"email": "test@example.com", "amount": 2, "reason": "test"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["credits_added"] == 2
        assert data["new_balance"] == 12

        mock_user_service.add_credits.assert_called_once_with(
            email="test@example.com",
            amount=2,
            reason="test",
            admin_email="admin@nomadkaraoke.com",
        )

    def test_add_credits_with_unicode_reason(self, client, mock_user_service):
        """Regression test for the original reported bug with CJK characters."""
        response = client.post(
            "/api/users/admin/credits",
            json={
                "email": "miravenn42@gmail.com",
                "amount": 2,
                "reason": "\u611f\u8c22\u60a8\u4f7f\u7528 Nomad Karaoke\uff01",
            },
        )
        assert response.status_code == 200

    def test_add_credits_sends_email(self, client, mock_email_service):
        response = client.post(
            "/api/users/admin/credits",
            json={"email": "test@example.com", "amount": 2, "reason": "promo"},
        )
        assert response.status_code == 200
        mock_email_service.send_credits_added.assert_called_once_with(
            "test@example.com", 2, 12
        )

    def test_add_credits_failure(self, client, mock_user_service):
        mock_user_service.add_credits.return_value = (False, 0, "User not found")
        response = client.post(
            "/api/users/admin/credits",
            json={"email": "nonexistent@example.com", "amount": 1, "reason": "test"},
        )
        assert response.status_code == 400


class TestDisableUser:
    def test_disable_user_success(self, client, mock_user_service):
        response = client.post("/api/users/admin/users/test@example.com/disable")
        assert response.status_code == 200
        mock_user_service.disable_user.assert_called_once_with(
            "test@example.com", admin_email="admin@nomadkaraoke.com"
        )

    def test_disable_user_not_found(self, client, mock_user_service):
        mock_user_service.disable_user.return_value = False
        response = client.post("/api/users/admin/users/nobody@example.com/disable")
        assert response.status_code == 404


class TestEnableUser:
    def test_enable_user_success(self, client, mock_user_service):
        response = client.post("/api/users/admin/users/test@example.com/enable")
        assert response.status_code == 200
        mock_user_service.enable_user.assert_called_once_with(
            "test@example.com", admin_email="admin@nomadkaraoke.com"
        )


class TestDeleteUser:
    def test_delete_user_success(self, client, mock_user_service):
        response = client.delete("/api/users/admin/users/test@example.com")
        assert response.status_code == 200
        mock_user_service.delete_user.assert_called_once_with(
            "test@example.com", admin_email="admin@nomadkaraoke.com"
        )

    def test_delete_user_not_found(self, client, mock_user_service):
        mock_user_service.delete_user.return_value = False
        response = client.delete("/api/users/admin/users/nobody@example.com")
        assert response.status_code == 404


class TestSetUserRole:
    def test_set_user_role_success(self, client, mock_user_service):
        response = client.post(
            "/api/users/admin/users/test@example.com/role",
            params={"role": "admin"},
        )
        assert response.status_code == 200
        mock_user_service.set_user_role.assert_called_once()
