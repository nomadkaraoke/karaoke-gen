"""Tests for PostmarkAdminService — admin email-history queries.

Merges the Postmark Messages API (last ~45 days, rich metadata) with our
persisted Firestore email_log (permanent), de-duplicating by MessageID, and
serves full message detail with a log fallback for expired Postmark content.
"""
from unittest.mock import MagicMock, patch

import backend.services.postmark_admin_service as pm
from backend.services.postmark_admin_service import PostmarkAdminService


def _log_doc(doc_id, data):
    d = MagicMock()
    d.id = doc_id
    d.to_dict.return_value = data
    return d


def _make_service(log_docs=None):
    svc = PostmarkAdminService(server_token="tok")
    fake_db = MagicMock()
    # collection().where().order_by().limit().stream() -> log_docs
    stream = MagicMock()
    stream.stream.return_value = log_docs or []
    fake_db.collection.return_value.where.return_value.order_by.return_value.limit.return_value = stream
    svc._db = fake_db
    return svc, fake_db


class TestHistoryMerge:
    def test_merges_and_dedupes_by_message_id(self):
        # Postmark returns m1 + m2; the log also has m2 (dupe) + m3 (older, purged).
        postmark_json = {
            "Messages": [
                {"MessageID": "m1", "Subject": "Welcome", "Recipients": ["u@x.com"],
                 "From": "gen@x.com", "ReceivedAt": "2026-08-20T10:00:00Z", "Status": "Sent"},
                {"MessageID": "m2", "Subject": "Receipt", "Recipients": ["u@x.com"],
                 "From": "gen@x.com", "ReceivedAt": "2026-08-21T10:00:00Z", "Status": "Sent"},
            ]
        }
        log_docs = [
            _log_doc("m2", {"postmark_message_id": "m2", "recipient": "u@x.com",
                            "subject": "Receipt", "html_content": "<p>r</p>",
                            "created_at": "2026-08-21T10:00:00Z", "email_type": "credits_added"}),
            _log_doc("auto3", {"postmark_message_id": "m3", "recipient": "u@x.com",
                               "subject": "Old", "html_content": "<p>old</p>",
                               "created_at": "2026-07-01T10:00:00Z", "email_type": "welcome"}),
        ]
        svc, _ = _make_service(log_docs)

        resp = MagicMock(status_code=200)
        resp.json.return_value = postmark_json
        with patch.object(pm.requests, "get", return_value=resp):
            history = svc.get_user_email_history("u@x.com")

        ids = {e["message_id"] for e in history["emails"]}
        assert ids == {"m1", "m2", "m3"}  # dupe m2 collapsed, m3 from log preserved
        assert history["count"] == 3
        assert history["postmark_available"] is True
        # Newest first
        sent = [e["sent_at"] for e in history["emails"]]
        assert sent == sorted(sent, reverse=True)
        # m2 present in both sources → flagged as having stored HTML
        m2 = next(e for e in history["emails"] if e["message_id"] == "m2")
        assert m2["source"] == "postmark"
        assert m2["has_stored_html"] is True

    def test_no_postmark_token_returns_log_only(self):
        svc, _ = _make_service([
            _log_doc("auto1", {"postmark_message_id": None, "recipient": "u@x.com",
                               "subject": "Hi", "html_content": "<p>h</p>",
                               "created_at": "2026-08-01T00:00:00Z"}),
        ])
        svc.server_token = None
        history = svc.get_user_email_history("u@x.com")
        assert history["postmark_available"] is False
        assert history["count"] == 1
        assert history["emails"][0]["source"] == "log"

    def test_postmark_error_degrades_to_log(self):
        svc, _ = _make_service([])
        resp = MagicMock(status_code=500, text="server error")
        with patch.object(pm.requests, "get", return_value=resp):
            history = svc.get_user_email_history("u@x.com")
        assert history["count"] == 0  # no crash, empty result


class TestEmailDetail:
    def test_postmark_detail_parses_events(self):
        svc, _ = _make_service()
        details = {
            "MessageID": "m1", "Subject": "Welcome", "From": "gen@x.com",
            "Recipients": ["u@x.com"], "HtmlBody": "<h1>Hi</h1>", "TextBody": "Hi",
            "ReceivedAt": "2026-08-20T10:00:00Z", "MessageStream": "outbound",
            "MessageEvents": [
                {"Type": "Delivered", "ReceivedAt": "2026-08-20T10:01:00Z", "Details": {}},
                {"Type": "Opened", "ReceivedAt": "2026-08-20T11:00:00Z", "Details": {}},
                {"Type": "Opened", "ReceivedAt": "2026-08-20T12:00:00Z", "Details": {}},
            ],
        }
        resp = MagicMock(status_code=200)
        resp.json.return_value = details
        with patch.object(pm.requests, "get", return_value=resp):
            detail = svc.get_email_detail("m1")
        assert detail["html_body"] == "<h1>Hi</h1>"
        assert detail["status"] == "Delivered"
        assert detail["open_count"] == 2
        assert detail["delivered_at"] == "2026-08-20T10:01:00+00:00" or detail["delivered_at"].startswith("2026-08-20T10:01:00")

    def test_bounced_status_from_events(self):
        svc, _ = _make_service()
        details = {
            "MessageID": "m9", "Subject": "x", "HtmlBody": "<p>x</p>",
            "MessageEvents": [{"Type": "Bounced", "ReceivedAt": "2026-08-20T10:01:00Z",
                               "Details": {"Type": "HardBounce"}}],
        }
        resp = MagicMock(status_code=200)
        resp.json.return_value = details
        with patch.object(pm.requests, "get", return_value=resp):
            detail = svc.get_email_detail("m9")
        assert detail["status"] == "Bounced"
        assert detail["bounce"] == {"Type": "HardBounce"}

    def test_postmark_404_falls_back_to_log(self):
        svc, fake_db = _make_service()
        # Postmark 404 (older than retention) → read stored copy by message id.
        snap = MagicMock()
        snap.exists = True
        snap.to_dict.return_value = {
            "postmark_message_id": "old1", "recipient": "u@x.com", "subject": "Old",
            "html_content": "<p>stored</p>", "created_at": "2026-06-01T00:00:00Z",
            "email_type": "welcome",
        }
        fake_db.collection.return_value.document.return_value.get.return_value = snap

        resp = MagicMock(status_code=404, text="not found")
        with patch.object(pm.requests, "get", return_value=resp):
            detail = svc.get_email_detail("old1")
        assert detail is not None
        assert detail["source"] == "log"
        assert detail["html_body"] == "<p>stored</p>"

    def test_source_log_skips_postmark(self):
        svc, fake_db = _make_service()
        snap = MagicMock()
        snap.exists = True
        snap.to_dict.return_value = {"postmark_message_id": "x", "recipient": "u@x.com",
                                     "subject": "S", "html_content": "<p>b</p>",
                                     "created_at": "2026-06-01T00:00:00Z"}
        fake_db.collection.return_value.document.return_value.get.return_value = snap

        with patch.object(pm.requests, "get") as get_mock:
            detail = svc.get_email_detail("x", source="log")
        get_mock.assert_not_called()  # log source must not hit Postmark
        assert detail["source"] == "log"
        assert detail["html_body"] == "<p>b</p>"
