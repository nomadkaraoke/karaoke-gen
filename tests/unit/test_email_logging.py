"""Tests for email persistence (admin email-history feature).

Every send routes through EmailService._log_and_send, which:
- captures Postmark's MessageID, and
- best-effort writes an ``email_log`` Firestore doc — without ever breaking the send.
"""
from unittest.mock import MagicMock, patch

import pytest

import backend.services.email_service as email_module
from backend.services.email_service import (
    EmailService,
    PostmarkEmailProvider,
    SendResult,
)


def _fake_response(status_code=200, message_id="mid-123"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {"MessageID": message_id, "ErrorCode": 0, "Message": "OK"}
    return resp


class TestPostmarkMessageIdCapture:
    def test_detailed_send_captures_message_id(self):
        provider = PostmarkEmailProvider("tok", "gen@nomadkaraoke.com")
        with patch.object(email_module.requests, "post", return_value=_fake_response(message_id="abc-999")):
            result = provider.send_email_detailed("u@example.com", "Hi", "<p>hi</p>")
        assert isinstance(result, SendResult)
        assert result.success is True
        assert result.message_id == "abc-999"

    def test_send_email_still_returns_bool(self):
        provider = PostmarkEmailProvider("tok", "gen@nomadkaraoke.com")
        with patch.object(email_module.requests, "post", return_value=_fake_response()):
            assert provider.send_email("u@example.com", "Hi", "<p>hi</p>") is True

    def test_failed_send_reports_no_message_id(self):
        provider = PostmarkEmailProvider("tok", "gen@nomadkaraoke.com")
        with patch.object(email_module.requests, "post", return_value=_fake_response(status_code=422)):
            result = provider.send_email_detailed("u@example.com", "Hi", "<p>hi</p>")
        assert result.success is False
        assert result.message_id is None


@pytest.fixture
def postmark_service(monkeypatch):
    """An EmailService backed by a (mocked) Postmark provider."""
    monkeypatch.setenv("POSTMARK_SERVER_TOKEN", "tok")
    monkeypatch.setenv("EMAIL_FROM", "gen@nomadkaraoke.com")
    with patch("backend.config.is_production", return_value=False):
        svc = EmailService()
    assert isinstance(svc.provider, PostmarkEmailProvider)
    return svc


class TestLogAndSend:
    def test_persists_email_log_with_derived_type(self, postmark_service):
        captured = {}

        class FakeDoc:
            def set(self, doc):
                captured["doc"] = doc

        class FakeCollection:
            def document(self, doc_id):
                captured["doc_id"] = doc_id
                return FakeDoc()

            def add(self, doc):
                captured["doc"] = doc

        fake_db = MagicMock()
        fake_db.collection.return_value = FakeCollection()

        with patch.object(email_module.requests, "post", return_value=_fake_response(message_id="mid-777")), \
             patch("backend.services.firestore_service.FirestoreService") as FS:
            FS.return_value.db = fake_db
            ok = postmark_service.send_credits_added("Buyer@Example.com", 5, 10)

        assert ok is True
        assert captured["doc_id"] == "mid-777"  # keyed by Postmark MessageID
        doc = captured["doc"]
        assert doc["recipient"] == "buyer@example.com"  # normalized
        assert doc["postmark_message_id"] == "mid-777"
        assert doc["email_type"] == "credits_added"  # derived from send_credits_added
        assert doc["html_content"]
        assert doc["message_stream"] == "outbound"

    def test_firestore_failure_never_breaks_send(self, postmark_service):
        with patch.object(email_module.requests, "post", return_value=_fake_response()), \
             patch("backend.services.firestore_service.FirestoreService", side_effect=RuntimeError("boom")):
            # Send must still succeed even though persistence blew up.
            ok = postmark_service.send_credits_added("u@example.com", 1, 1)
        assert ok is True

    def test_no_persistence_when_send_fails(self, postmark_service):
        with patch.object(email_module.requests, "post", return_value=_fake_response(status_code=500)), \
             patch("backend.services.firestore_service.FirestoreService") as FS:
            ok = postmark_service.send_credits_added("u@example.com", 1, 1)
        assert ok is False
        FS.assert_not_called()  # failed sends are not logged
