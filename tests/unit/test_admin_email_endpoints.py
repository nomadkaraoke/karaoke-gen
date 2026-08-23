"""Tests for the admin email-history endpoints in backend/api/routes/admin.py.

Verifies wiring + auth: GET /admin/users/{email}/emails and
GET /admin/emails/{message_id} (with 404 on missing detail).
"""
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def mock_postmark_service():
    svc = MagicMock()
    svc.get_user_email_history.return_value = {
        "email": "u@x.com", "count": 1, "postmark_available": True,
        "emails": [{"message_id": "m1", "source": "postmark", "subject": "Hi"}],
    }
    svc.get_email_detail.return_value = {
        "message_id": "m1", "source": "postmark", "subject": "Hi",
        "html_body": "<h1>Hi</h1>", "status": "Delivered",
    }
    return svc


@pytest.fixture
def client(mock_postmark_service):
    mock_creds = MagicMock()
    mock_creds.universe_domain = "googleapis.com"
    with patch("backend.services.firestore_service.firestore"), \
         patch("backend.services.storage_service.storage"), \
         patch("google.auth.default", return_value=(mock_creds, "test-project")), \
         patch("backend.services.postmark_admin_service.get_postmark_admin_service",
               return_value=mock_postmark_service):
        from fastapi import FastAPI
        from backend.api.routes.admin import router
        from backend.api.dependencies import require_admin

        app = FastAPI()
        app.include_router(router, prefix="/api")
        app.dependency_overrides[require_admin] = lambda: ("admin@nomadkaraoke.com", None, -1)
        yield TestClient(app)


def test_get_user_emails(client, mock_postmark_service):
    resp = client.get("/api/admin/users/u@x.com/emails")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["emails"][0]["message_id"] == "m1"
    mock_postmark_service.get_user_email_history.assert_called_once_with("u@x.com")


def test_get_email_detail_default_source(client, mock_postmark_service):
    resp = client.get("/api/admin/emails/m1")
    assert resp.status_code == 200
    assert resp.json()["html_body"] == "<h1>Hi</h1>"
    mock_postmark_service.get_email_detail.assert_called_once_with("m1", source="postmark")


def test_get_email_detail_log_source(client, mock_postmark_service):
    client.get("/api/admin/emails/old1?source=log")
    mock_postmark_service.get_email_detail.assert_called_once_with("old1", source="log")


def test_get_email_detail_404(client, mock_postmark_service):
    mock_postmark_service.get_email_detail.return_value = None
    resp = client.get("/api/admin/emails/missing")
    assert resp.status_code == 404
