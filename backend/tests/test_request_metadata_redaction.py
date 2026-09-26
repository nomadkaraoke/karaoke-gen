"""request_metadata.custom_headers must never store credential headers raw —
GET /api/jobs/{id} returns request_metadata to the job owner."""
import pytest
from starlette.requests import Request

from backend.api.routes import audio_search, file_upload
from backend.utils.request_helpers import REDACTED, collect_custom_headers

SECRET_HEADERS = {
    "X-Kjbox-Secret": "kj-secret-value",
    "X-Admin-Token": "admin-token-value",
    "X-KH-Forwarder-Secret": "kh-secret-value",
    "X-E2E-Bypass-Key": "bypass-value",
    "X-Api-Key": "api-key-value",
    "X-Authorization": "Bearer abc",
    "X-User-Password": "hunter2",
}
PLAIN_HEADERS = {"X-Client-Id": "kjbox", "X-Environment": "production", "X-Referral-Code": "abc"}


def _request(headers):
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "method": "POST", "path": "/", "headers": raw,
                    "client": ("1.2.3.4", 1234)})


def test_collect_custom_headers_redacts_secrets_keeps_others():
    out = collect_custom_headers({**{k.lower(): v for k, v in {**SECRET_HEADERS, **PLAIN_HEADERS}.items()},
                                  "x-forwarded-for": "9.9.9.9", "user-agent": "ua"})
    for name in SECRET_HEADERS:
        assert out[name.lower()] == REDACTED
    for name, value in PLAIN_HEADERS.items():
        assert out[name.lower()] == value
    assert "x-forwarded-for" not in out and "user-agent" not in out


@pytest.mark.parametrize("extract", [audio_search.extract_request_metadata,
                                     file_upload.extract_request_metadata])
def test_extract_request_metadata_never_stores_secret_values(extract):
    meta = extract(_request({**SECRET_HEADERS, **PLAIN_HEADERS}))
    blob = repr(meta)
    for value in SECRET_HEADERS.values():
        assert value not in blob
    assert meta["client_id"] == "kjbox"
    assert meta["custom_headers"]["x-kjbox-secret"] == REDACTED
    assert meta["custom_headers"]["x-client-id"] == "kjbox"


def test_session_and_jwt_headers_are_redacted():
    from backend.utils.request_helpers import REDACTED, collect_custom_headers
    out = collect_custom_headers({"X-JWT": "eyJ...", "X-Session-Id": "sess-abc", "X-Client-Id": "kjbox"})
    assert out["X-JWT"] == REDACTED
    assert out["X-Session-Id"] == REDACTED
    assert out["X-Client-Id"] == "kjbox"
