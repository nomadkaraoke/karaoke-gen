"""Tests for first-touch signup attribution (UTM + referrer -> signup_source)."""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.dependencies import require_admin
from backend.api.routes.users import router
from backend.models.user import SendMagicLinkRequest
from backend.services.auth_service import AuthResult, UserType
from backend.services.signup_attribution import clean_attribution, derive_signup_source
from backend.services.user_service import UserService, get_user_service


class TestCleanAttribution:
    def test_keeps_whitelisted_utm_and_lowercases_source(self):
        out = clean_attribution({
            "utm_source": "YouTube",
            "utm_medium": "Description",
            "utm_campaign": "make_cta",
            "utm_content": "Mixed Case Kept",
            "landing_path": "/en/r",
            "captured_at": "2026-10-14T00:00:00Z",
            "evil": "dropped",
        })
        assert out == {
            "utm_source": "youtube",
            "utm_medium": "description",
            "utm_campaign": "make_cta",
            "utm_content": "Mixed Case Kept",
            "landing_path": "/en/r",
            "captured_at": "2026-10-14T00:00:00Z",
        }

    def test_referrer_reduced_to_bare_host(self):
        out = clean_attribution({"referrer": "https://m.youtube.com/watch?v=abc"})
        assert out["referrer"] == "youtube.com"

    def test_gen_referrer_is_not_a_signal(self):
        assert clean_attribution({"referrer": "https://gen.nomadkaraoke.com/en/app", "landing_path": "/en"}) is None
        assert clean_attribution({"referrer": "api.nomadkaraoke.com"}) is None

    def test_empty_or_garbage_returns_none(self):
        assert clean_attribution(None) is None
        assert clean_attribution({}) is None
        assert clean_attribution({"utm_source": 123, "landing_path": "/x"}) is None
        assert clean_attribution("not a dict") is None

    def test_values_are_length_capped_and_stripped_of_control_chars(self):
        out = clean_attribution({"utm_source": "a\x00b\n" + "x" * 500})
        assert "\x00" not in out["utm_source"]
        assert "\n" not in out["utm_source"]
        assert len(out["utm_source"]) == 200


class TestDeriveSignupSource:
    def test_utm_source_wins(self):
        assert derive_signup_source({"utm_source": "youtube", "referrer": "google.com"}) == "youtube"

    @pytest.mark.parametrize("host,label", [
        ("youtube.com", "youtube"),
        ("youtu.be", "youtube"),
        ("google.co.uk", "google"),
        ("google.com", "google"),
        ("l.facebook.com", "facebook"),
        ("t.co", "twitter"),
        ("www.nomadkaraoke.com", "website"),
        ("decide.nomadkaraoke.com", "decide"),
        ("sing.nomadkaraoke.com", "kjbox"),
        ("some-blog.example", "some-blog.example"),
    ])
    def test_referrer_channel(self, host, label):
        cleaned = clean_attribution({"referrer": f"https://{host}/x"})
        assert derive_signup_source(cleaned) == label

    def test_none_without_signal(self):
        assert derive_signup_source(None) is None
        assert derive_signup_source({"landing_path": "/"}) is None


class TestCreateMagicLinkRecordsAttribution:
    def _service(self):
        svc = UserService.__new__(UserService)  # bypass __init__ (no real Firestore client)
        svc.db = MagicMock()
        svc.get_or_create_user = MagicMock()
        return svc

    def test_attribution_passed_to_user_creation(self):
        svc = self._service()
        svc.create_magic_link(
            "New@Example.com",
            attribution={"utm_source": "YouTube", "utm_campaign": "make_cta", "landing_path": "/en/r"},
        )
        kwargs = svc.get_or_create_user.call_args.kwargs
        assert kwargs["signup_source"] == "youtube"
        assert kwargs["signup_attribution"] == {
            "utm_source": "youtube", "utm_campaign": "make_cta", "landing_path": "/en/r",
        }

    def test_no_attribution_records_nothing(self):
        svc = self._service()
        svc.create_magic_link("a@example.com")
        kwargs = svc.get_or_create_user.call_args.kwargs
        assert kwargs["signup_source"] is None
        assert kwargs["signup_attribution"] is None

    def test_existing_user_is_not_overwritten(self):
        """get_or_create_user returns existing users untouched, so a later login with
        different UTMs can't rewrite the first-touch source."""
        svc = UserService.__new__(UserService)
        existing = Mock(signup_source="google")
        svc.get_user = MagicMock(return_value=existing)
        svc._save_user = MagicMock()
        out = svc.get_or_create_user("a@example.com", signup_source="youtube", signup_attribution={"utm_source": "youtube"})
        assert out is existing
        svc._save_user.assert_not_called()


def test_malformed_referrer_is_ignored():
    assert clean_attribution({"referrer": "//[x", "utm_source": "yt"}) == {"utm_source": "yt"}


def test_attribution_payload_is_bounded():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        SendMagicLinkRequest(email="a@example.com", attribution={f"k{i}": "v" for i in range(17)})
    with pytest.raises(ValidationError):
        SendMagicLinkRequest(email="a@example.com", attribution={"utm_source": "x" * 501})


def test_send_magic_link_request_accepts_attribution():
    req = SendMagicLinkRequest(email="a@example.com", attribution={"utm_source": "youtube", "utm_term": None})
    assert req.attribution["utm_source"] == "youtube"


# --- Admin attribution summary endpoint ---

app = FastAPI()
app.include_router(router, prefix="/api")


def _mock_admin():
    return AuthResult(
        is_valid=True, user_type=UserType.ADMIN, remaining_uses=-1,
        message="ok", user_email="admin@nomadkaraoke.com", is_admin=True,
    )


def _doc(**data):
    d = Mock()
    d.to_dict.return_value = data
    return d


@pytest.fixture
def summary_client():
    now = datetime.utcnow()
    recent = (now - timedelta(days=5)).isoformat()
    docs = [
        _doc(email="a@example.com", created_at=recent, signup_source="youtube",
             email_verified=True, total_jobs_created=2, total_spent=2400),
        _doc(email="b@example.com", created_at=now - timedelta(days=1), signup_source="youtube",
             email_verified=True, total_jobs_created=1, total_spent=0),
        _doc(email="c@example.com", created_at=recent, signup_source=None,
             email_verified=False, total_jobs_created=0, total_spent=0),
        # Excluded: too old, tenant user, test email
        _doc(email="old@example.com", created_at=(now - timedelta(days=400)).isoformat(), signup_source="youtube"),
        _doc(email="t@example.com", created_at=recent, tenant_id="vocalstar", signup_source="youtube"),
        _doc(email="x@inbox.testmail.app", created_at=recent, signup_source="youtube"),
    ]
    service = Mock()
    service.db.collection.return_value.select.return_value.stream.return_value = docs
    app.dependency_overrides[require_admin] = _mock_admin
    app.dependency_overrides[get_user_service] = lambda: service
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_attribution_summary_groups_funnel_by_source(summary_client):
    res = summary_client.get("/api/users/admin/attribution-summary?days=90")
    assert res.status_code == 200
    body = res.json()
    assert body["days"] == 90
    assert body["total_signups"] == 3
    rows = {r["source"]: r for r in body["sources"]}
    assert rows["youtube"] == {
        "source": "youtube", "signups": 2, "verified": 2, "made_job": 2, "paid": 1, "revenue_cents": 2400,
    }
    assert rows["direct / unknown"]["signups"] == 1
    assert rows["direct / unknown"]["paid"] == 0
    # Paying sources sort first
    assert body["sources"][0]["source"] == "youtube"
