"""Tests for the free-to-paid conversion emails (E1 offer, E2 reminder, promo discount)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.services import conversion_emails as ce

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch):
    monkeypatch.setattr(ce, "_now", lambda: NOW)


@pytest.fixture
def settings(monkeypatch):
    s = SimpleNamespace(
        conversion_emails_enabled=True,
        conversion_offer_percent=25,
        conversion_offer_hours=48,
        conversion_e1_delay_hours=2,
        conversion_e2_hours_before_expiry=8,
    )
    monkeypatch.setattr(ce, "get_settings", lambda: s)
    return s


class FakeDoc:
    def __init__(self, data):
        self._data = data
        self.id = data["email"]

    def to_dict(self):
        return dict(self._data)


class FakeDB:
    """Minimal Firestore stand-in: where(field, op, value).stream() over dict rows."""

    def __init__(self, rows):
        self.rows = rows

    def collection(self, name):
        assert name == "gen_users"
        return self

    def where(self, field, op, value):
        ops = {"<=": lambda a, b: a <= b, ">": lambda a, b: a > b}
        matched = [r for r in self.rows if r.get(field) is not None and ops[op](r[field], value)]
        return SimpleNamespace(stream=lambda: [FakeDoc(r) for r in matched])


def _user(**kw):
    base = dict(
        email="fan@example.com", total_spent=0, total_jobs_completed=1, tenant_id=None,
        conversion_e1_due_at=None, conversion_e1_sent_at=None,
        promo_discount_percent=None, promo_discount_expires_at=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class TestOnJobCompleted:
    def _job(self, **kw):
        return SimpleNamespace(**{"user_email": "fan@example.com", "made_for_you": False, "tenant_id": None,
                                  "artist": "Queen", "title": "Bohemian Rhapsody", **kw})

    def test_first_unpaid_video_is_scheduled(self, settings):
        us = MagicMock()
        us.get_user.return_value = _user()
        assert ce.on_job_completed(self._job(), us) is True
        kwargs = us.update_user.call_args.kwargs
        assert kwargs["conversion_e1_due_at"] == NOW + timedelta(hours=2)
        assert kwargs["first_video_artist"] == "Queen"

    @pytest.mark.parametrize("user_kw", [
        {"total_spent": 1000},
        {"total_jobs_completed": 2},
        {"conversion_e1_due_at": NOW},
        {"tenant_id": "vocalstar"},
    ])
    def test_ineligible_users_are_skipped(self, settings, user_kw):
        us = MagicMock()
        us.get_user.return_value = _user(**user_kw)
        assert ce.on_job_completed(self._job(), us) is False
        us.update_user.assert_not_called()

    @pytest.mark.parametrize("job_kw", [
        {"made_for_you": True},
        {"tenant_id": "vocalstar"},
        {"user_email": "x@inbox.testmail.app"},
        {"user_email": None},
    ])
    def test_ineligible_jobs_are_skipped(self, settings, job_kw):
        us = MagicMock()
        us.get_user.return_value = _user()
        assert ce.on_job_completed(self._job(**job_kw), us) is False


class TestActivePromo:
    def test_active_and_expired(self):
        assert ce.active_promo(_user(promo_discount_percent=25, promo_discount_expires_at=NOW + timedelta(hours=1))) == (
            25, NOW + timedelta(hours=1))
        assert ce.active_promo(_user(promo_discount_percent=25, promo_discount_expires_at=NOW - timedelta(seconds=1))) is None
        assert ce.active_promo(_user()) is None

    def test_naive_and_string_datetimes(self):
        naive = (NOW + timedelta(hours=3)).replace(tzinfo=None)
        assert ce.active_promo(_user(promo_discount_percent=20, promo_discount_expires_at=naive))[0] == 20
        iso = (NOW + timedelta(hours=3)).isoformat()
        assert ce.active_promo(_user(promo_discount_percent=20, promo_discount_expires_at=iso))[0] == 20


class TestPromoCoupon:
    def _patch(self, user):
        us = MagicMock()
        us.get_user.return_value = user
        ss = MagicMock()
        ss.get_or_create_promo_coupon.side_effect = lambda pct: f"conversion-offer-{pct}pct"
        return (patch("backend.services.user_service.get_user_service", return_value=us),
                patch("backend.services.stripe_service.get_stripe_service", return_value=ss))

    def test_promo_beats_smaller_referral(self):
        p1, p2 = self._patch(_user(promo_discount_percent=25, promo_discount_expires_at=NOW + timedelta(hours=5)))
        with p1, p2:
            assert ce.promo_coupon_for_checkout("fan@example.com", better_than_percent=20) == "conversion-offer-25pct"
            assert ce.promo_coupon_for_checkout("fan@example.com", better_than_percent=50) is None

    def test_no_promo(self):
        p1, p2 = self._patch(_user())
        with p1, p2:
            assert ce.promo_coupon_for_checkout("fan@example.com") is None


class TestProcess:
    def _run(self, rows, settings):
        db = FakeDB(rows)
        es = MagicMock(frontend_url="https://gen.nomadkaraoke.com")
        us = MagicMock()
        return ce.process_conversion_emails(db=db, email_service=es, user_service=us), es, us

    def test_disabled_is_noop(self, settings):
        settings.conversion_emails_enabled = False
        result, es, us = self._run([{"email": "a@example.com", "conversion_e1_due_at": NOW}], settings)
        assert result["status"] == "disabled"
        es.send_conversion_offer.assert_not_called()

    def test_e1_sends_and_grants_promo(self, settings):
        row = {"email": "a@example.com", "conversion_e1_due_at": NOW - timedelta(minutes=5), "total_spent": 0,
               "locale": "es", "first_video_artist": "Queen", "first_video_title": "Bohemian Rhapsody"}
        result, es, us = self._run([row], settings)
        assert result["e1_sent"] == 1
        kwargs = es.send_conversion_offer.call_args.kwargs
        assert kwargs["percent"] == 25 and kwargs["locale"] == "es" and kwargs["artist"] == "Queen"
        assert kwargs["buy_url"].startswith("https://gen.nomadkaraoke.com/es/app?buy=1&utm_source=email")
        assert "utm_campaign=conv_e1" in kwargs["buy_url"]
        update = us.update_user.call_args.kwargs
        assert update["promo_discount_percent"] == 25
        assert update["promo_discount_expires_at"] == NOW + timedelta(hours=48)
        assert update["conversion_e1_sent_at"] == NOW

    def test_e1_not_due_yet_or_already_sent(self, settings):
        rows = [
            {"email": "future@example.com", "conversion_e1_due_at": NOW + timedelta(hours=1)},
            {"email": "done@example.com", "conversion_e1_due_at": NOW - timedelta(hours=1), "conversion_e1_sent_at": NOW},
        ]
        result, es, _ = self._run(rows, settings)
        assert result["e1_sent"] == 0
        es.send_conversion_offer.assert_not_called()

    @pytest.mark.parametrize("row", [
        {"email": "paid@example.com", "conversion_e1_due_at": NOW - timedelta(hours=1), "total_spent": 1000},
        {"email": "stale@example.com", "conversion_e1_due_at": NOW - timedelta(hours=30), "total_spent": 0},
    ])
    def test_e1_skips_paid_or_stale_and_marks_done(self, settings, row):
        result, es, us = self._run([row], settings)
        assert result["e1_sent"] == 0
        es.send_conversion_offer.assert_not_called()
        assert us.update_user.call_args.kwargs["conversion_e1_skipped"] is True

    def test_e2_inside_window(self, settings):
        row = {"email": "a@example.com", "conversion_e1_sent_at": NOW - timedelta(hours=41),
               "promo_discount_percent": 25, "promo_discount_expires_at": NOW + timedelta(hours=7), "total_spent": 0}
        result, es, us = self._run([row], settings)
        assert result["e2_sent"] == 1
        kwargs = es.send_conversion_offer_reminder.call_args.kwargs
        assert kwargs["hours_left"] == 7 and kwargs["percent"] == 25
        assert "utm_campaign=conv_e2" in kwargs["buy_url"]

    @pytest.mark.parametrize("extra", [
        {"promo_discount_expires_at": NOW + timedelta(hours=20)},  # too early
        {"conversion_e2_sent_at": NOW - timedelta(hours=1)},  # already sent
        {"total_spent": 2400},  # bought
    ])
    def test_e2_skips(self, settings, extra):
        row = {"email": "a@example.com", "conversion_e1_sent_at": NOW - timedelta(hours=41),
               "promo_discount_percent": 25, "promo_discount_expires_at": NOW + timedelta(hours=7), "total_spent": 0}
        row.update(extra)
        result, es, _ = self._run([row], settings)
        assert result["e2_sent"] == 0


class TestEmailRendering:
    @pytest.fixture
    def email_service(self):
        from backend.services.email_service import EmailService
        svc = EmailService.__new__(EmailService)
        svc.settings = SimpleNamespace(commercial_email_postal_address="Nomad Karaoke, 1 Test St")
        svc.frontend_url = "https://gen.nomadkaraoke.com"
        svc._log_and_send = MagicMock(return_value=True)
        return svc

    def test_offer_goes_on_broadcast_stream_with_postal_address(self, email_service):
        email_service.send_conversion_offer(
            "a@example.com", "Queen", "Bohemian Rhapsody", 25, 48,
            "https://gen.nomadkaraoke.com/en/app?buy=1", "https://gen.nomadkaraoke.com/en/order",
        )
        kwargs = email_service._log_and_send.call_args.kwargs
        assert kwargs["message_stream"] == "broadcast"
        assert "25% off any credit pack for the next 48 hours" in kwargs["html_content"]
        assert "Queen - Bohemian Rhapsody" in kwargs["html_content"]
        assert "1 Test St" in kwargs["html_content"] and "1 Test St" in kwargs["text_content"]

    def test_reminder_is_broadcast(self, email_service):
        email_service.send_conversion_offer_reminder("a@example.com", 25, 7, "https://x")
        kwargs = email_service._log_and_send.call_args.kwargs
        assert kwargs["message_stream"] == "broadcast"
        assert kwargs["subject"] == "Your 25% off ends in 7 hours"

    def test_last_chance_is_transactional(self, email_service):
        email_service.send_review_last_chance("a@example.com", "Queen", "Song", "https://x/review", 6)
        kwargs = email_service._log_and_send.call_args.kwargs
        assert "message_stream" not in kwargs
        assert kwargs["subject"] == "6 hours left to finish your karaoke video: Queen - Song"


class TestPostmarkStream:
    def test_broadcast_stream_in_payload(self):
        from backend.services.email_service import PostmarkEmailProvider
        p = PostmarkEmailProvider("tok", "gen@nomadkaraoke.com")
        resp = MagicMock(status_code=200)
        resp.json.return_value = {"MessageID": "m1"}
        with patch.object(p, "_post_with_retry", return_value=resp) as post:
            assert p.send_email("a@example.com", "s", "<p>x</p>", message_stream="broadcast")
            assert post.call_args.args[0]["MessageStream"] == "broadcast"
            assert p.send_email("a@example.com", "s", "<p>x</p>")
            assert post.call_args.args[0]["MessageStream"] == "outbound"
