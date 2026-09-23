"""Unit tests for the KaraokeHunt retired-app request conversion worker.

Mocks the user/job/board/email layers so we exercise process_intake's routing:
new-user credit grant → job, existing-user credit consumption, no-credits →
requests board, daily cap fall-through, dedup, idempotent re-entry, and the
conservative search auto-select (park on anything unconfident).
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.job import JobStatus
from backend.services.audio_search_service import NoResultsError
from backend.services.song_request_service import SubmissionRateLimited
from backend.workers import karaokehunt_conversion as khc


# ---------------------------------------------------------------- fakes

class FakeSnap:
    def __init__(self, data):
        self._data = data

    @property
    def exists(self):
        return self._data is not None

    def to_dict(self):
        return dict(self._data)


class FakeRef:
    def __init__(self, store, doc_id):
        self.store = store
        self.doc_id = doc_id

    def get(self):
        return FakeSnap(self.store.get(self.doc_id))

    def set(self, data):
        self.store[self.doc_id] = dict(data)

    def update(self, data):
        self.store[self.doc_id].update(data)


class FakeCollection:
    """Filters are ignored — stream() yields every stored doc, mirroring the
    worst case the helpers' in-Python filtering must handle anyway."""

    def __init__(self, store):
        self.store = store

    def document(self, doc_id):
        return FakeRef(self.store, doc_id)

    def where(self, *args, **kwargs):
        return self

    def stream(self):
        return iter(FakeSnap(dict(d)) for d in self.store.values())


class FakeDb:
    def __init__(self):
        self.store = {}

    def collection(self, name):
        return FakeCollection(self.store)


def _intake_doc(**over):
    base = dict(
        id="doc1", email="fan@example.com", artist="Olivia Rodrigo",
        title="good 4 u", input_url="", raw={}, client_ip="1.2.3.4",
        user_agent="app", dedupe_key="olivia rodrigo|good 4 u",
        outcome=None, job_id=None, board_request_id=None, new_user=False,
        credit_granted=False, email_sent=False, error=None,
        created_at=datetime.now(timezone.utc), processed_at=None,
    )
    base.update(over)
    return base


def _settings(cap=3):
    return SimpleNamespace(
        karaokehunt_daily_job_cap=cap,
        default_enable_cdg=True, default_enable_txt=True, default_brand_prefix="NOMAD",
        default_enable_youtube_upload=True, default_youtube_description="desc",
        default_discord_webhook_url=None, default_dropbox_path=None,
        default_gdrive_folder_id=None,
    )


@pytest.fixture
def fake_db(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr(khc, "_db_singleton", db)
    return db


@pytest.fixture
def quiet_side_paths(monkeypatch):
    """Silence the query/bonus helpers that hit Firestore/GCS in production."""
    monkeypatch.setattr(khc, "_recent_duplicate_exists", lambda db, doc: False)
    monkeypatch.setattr(khc, "_jobs_today", lambda db: 0)
    monkeypatch.setattr(khc, "_community_version_url", AsyncMock(return_value=None))


def _user_service(existing=True, credits=1, add_ok=True):
    svc = MagicMock()
    svc.get_user.return_value = MagicMock() if existing else None
    svc.check_credits.return_value = credits
    svc.add_credits.return_value = (add_ok, credits, "ok" if add_ok else "boom")
    return svc


# ---------------------------------------------------------------- create_intake

class TestCreateIntake:
    def test_valid_payload_creates_pending_doc(self, fake_db):
        doc = khc.create_intake(
            {"email": "Fan@Example.com ", "artist": " Olivia Rodrigo",
             "title": "good 4 u", "input_url": ""},
            client_ip="9.9.9.9", user_agent="Dart/3.0",
        )
        assert doc["outcome"] is None
        assert doc["email"] == "fan@example.com"
        assert doc["artist"] == "Olivia Rodrigo"
        assert doc["dedupe_key"]
        assert fake_db.store[doc["id"]]["client_ip"] == "9.9.9.9"

    @pytest.mark.parametrize("payload", [
        {"email": "not-an-email", "artist": "A", "title": "T"},
        {"email": "", "artist": "A", "title": "T"},
        {"artist": "A", "title": "T"},
        {"email": "a@b.co", "artist": "", "title": "T"},
        {"email": "a@b.co", "artist": "A", "title": " "},
    ])
    def test_unusable_payloads_marked_invalid(self, fake_db, payload):
        doc = khc.create_intake(payload)
        assert doc["outcome"] == "invalid"
        assert fake_db.store[doc["id"]]["outcome"] == "invalid"

    def test_raw_payload_is_capped(self, fake_db):
        payload = {"email": "a@b.co", "artist": "A", "title": "T"}
        payload.update({f"junk{i}": "x" * 5000 for i in range(50)})
        doc = khc.create_intake(payload)
        assert len(doc["raw"]) <= 20
        assert all(len(v) <= 500 for v in doc["raw"].values())


# ---------------------------------------------------------------- process_intake

class TestProcessIntake:
    @pytest.mark.asyncio
    async def test_new_user_gets_account_credit_and_job(self, fake_db, quiet_side_paths, monkeypatch):
        fake_db.store["doc1"] = _intake_doc()
        svc = _user_service(existing=False, credits=1)
        monkeypatch.setattr(khc, "get_user_service", lambda: svc)
        monkeypatch.setattr(khc, "get_settings", _settings)
        make_job = AsyncMock(return_value={
            "status": "ok", "outcome": "job_created", "variant": "job", "job_id": "j1"})
        monkeypatch.setattr(khc, "_make_job", make_job)
        send = MagicMock(return_value=True)
        monkeypatch.setattr(khc, "_send_conversion_email", send)

        result = await khc.process_intake("doc1")

        assert result["status"] == "job_created"
        svc.get_or_create_user.assert_called_once_with("fan@example.com")
        svc.add_credits.assert_called_once_with(
            "fan@example.com", amount=1, reason=khc.CREDIT_REASON)
        assert fake_db.store["doc1"]["outcome"] == "job_created"
        assert fake_db.store["doc1"]["new_user"] is True
        assert fake_db.store["doc1"]["credit_granted"] is True
        # New users are told their first video is on us, not "we used your credit".
        assert send.call_args.kwargs["used_existing_credit"] is False
        assert send.call_args.kwargs["variant"] == "job"

    @pytest.mark.asyncio
    async def test_existing_user_with_credits_consumes_their_credit(
            self, fake_db, quiet_side_paths, monkeypatch):
        fake_db.store["doc1"] = _intake_doc()
        svc = _user_service(existing=True, credits=2)
        monkeypatch.setattr(khc, "get_user_service", lambda: svc)
        monkeypatch.setattr(khc, "get_settings", _settings)
        monkeypatch.setattr(khc, "_make_job", AsyncMock(return_value={
            "status": "ok", "outcome": "job_created", "variant": "job", "job_id": "j1"}))
        send = MagicMock(return_value=True)
        monkeypatch.setattr(khc, "_send_conversion_email", send)

        result = await khc.process_intake("doc1")

        assert result["status"] == "job_created"
        svc.add_credits.assert_not_called()
        assert send.call_args.kwargs["used_existing_credit"] is True

    @pytest.mark.asyncio
    async def test_existing_community_version_short_circuits_job(
            self, fake_db, monkeypatch):
        """A song with an existing community karaoke version must never spend
        generation resources — no job, no board; the email links to YouTube."""
        fake_db.store["doc1"] = _intake_doc()
        monkeypatch.setattr(khc, "_jobs_today", lambda db: 0)
        monkeypatch.setattr(khc, "_community_version_url",
                            AsyncMock(return_value="https://youtube.com/watch?v=abc"))
        svc = _user_service(existing=False, credits=1)
        monkeypatch.setattr(khc, "get_user_service", lambda: svc)
        monkeypatch.setattr(khc, "get_settings", _settings)
        make_job = AsyncMock()
        board = AsyncMock()
        monkeypatch.setattr(khc, "_make_job", make_job)
        monkeypatch.setattr(khc, "_submit_to_board", board)
        send = MagicMock(return_value=True)
        monkeypatch.setattr(khc, "_send_conversion_email", send)

        result = await khc.process_intake("doc1")

        assert result["status"] == "community_existing"
        make_job.assert_not_awaited()
        board.assert_not_awaited()
        # New users still get their account + conversion credit to keep.
        svc.add_credits.assert_called_once()
        assert send.call_args.kwargs["variant"] == "community"
        assert send.call_args.kwargs["community_url"] == "https://youtube.com/watch?v=abc"
        assert send.call_args.kwargs["is_new_user"] is True
        assert fake_db.store["doc1"]["outcome"] == "community_existing"
        # community_existing must be terminal AND count as the one-time freebie.
        assert "community_existing" in khc.TERMINAL_OUTCOMES
        assert "community_existing" in khc.CONVERTED_OUTCOMES

    @pytest.mark.asyncio
    async def test_community_short_circuit_existing_user_credits_untouched(
            self, fake_db, monkeypatch):
        fake_db.store["doc1"] = _intake_doc()
        monkeypatch.setattr(khc, "_jobs_today", lambda db: 0)
        monkeypatch.setattr(khc, "_community_version_url",
                            AsyncMock(return_value="https://youtube.com/watch?v=abc"))
        svc = _user_service(existing=True, credits=3)
        monkeypatch.setattr(khc, "get_user_service", lambda: svc)
        monkeypatch.setattr(khc, "get_settings", _settings)
        monkeypatch.setattr(khc, "_make_job", AsyncMock())
        send = MagicMock(return_value=True)
        monkeypatch.setattr(khc, "_send_conversion_email", send)

        result = await khc.process_intake("doc1")

        assert result["status"] == "community_existing"
        svc.add_credits.assert_not_called()
        assert send.call_args.kwargs["is_new_user"] is False

    @pytest.mark.asyncio
    async def test_second_request_ever_gets_uninstall_email_only(
            self, fake_db, monkeypatch):
        """One freebie EVER per email: a later request for a DIFFERENT song gets
        the uninstall email — no account changes, no job, no board."""
        fake_db.store["doc0"] = _intake_doc(id="doc0", outcome="job_created",
                                            job_id="j0")
        fake_db.store["doc1"] = _intake_doc(
            id="doc1", artist="Toto", title="Africa", dedupe_key="toto|africa")
        svc = _user_service()
        monkeypatch.setattr(khc, "get_user_service", lambda: svc)
        make_job = AsyncMock()
        board = AsyncMock()
        monkeypatch.setattr(khc, "_make_job", make_job)
        monkeypatch.setattr(khc, "_submit_to_board", board)
        send = MagicMock(return_value=True)
        monkeypatch.setattr(khc, "_send_conversion_email", send)

        result = await khc.process_intake("doc1")

        assert result["status"] == "repeat_request"
        assert result["email_sent"] is True
        make_job.assert_not_awaited()
        board.assert_not_awaited()
        svc.get_or_create_user.assert_not_called()
        svc.add_credits.assert_not_called()
        assert send.call_args.kwargs["variant"] == "uninstall"
        assert fake_db.store["doc1"]["outcome"] == "repeat_request"

    @pytest.mark.asyncio
    async def test_repeat_uninstall_email_is_throttled(self, fake_db, monkeypatch):
        """A hammering client can't make us spam: one uninstall email per window."""
        fake_db.store["doc0"] = _intake_doc(id="doc0", outcome="job_created")
        fake_db.store["docR"] = _intake_doc(
            id="docR", artist="Toto", title="Africa", dedupe_key="toto|africa",
            outcome="repeat_request", email_sent=True)
        fake_db.store["doc1"] = _intake_doc(
            id="doc1", artist="A-ha", title="Take On Me", dedupe_key="a-ha|take on me")
        send = MagicMock()
        monkeypatch.setattr(khc, "_send_conversion_email", send)

        result = await khc.process_intake("doc1")

        assert result["status"] == "repeat_request"
        assert result["email_sent"] is False
        send.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_credits_goes_to_requests_board(self, fake_db, quiet_side_paths, monkeypatch):
        fake_db.store["doc1"] = _intake_doc()
        svc = _user_service(existing=True, credits=0)
        monkeypatch.setattr(khc, "get_user_service", lambda: svc)
        monkeypatch.setattr(khc, "get_settings", _settings)
        board = AsyncMock(return_value={
            "status": "ok", "outcome": "board_submitted", "variant": "board",
            "board_request_id": "r1", "board_reason": "no_credits"})
        monkeypatch.setattr(khc, "_submit_to_board", board)
        send = MagicMock(return_value=True)
        monkeypatch.setattr(khc, "_send_conversion_email", send)

        result = await khc.process_intake("doc1")

        assert result["status"] == "board_submitted"
        board.assert_awaited_once_with(
            "fan@example.com", "Olivia Rodrigo", "good 4 u", "no_credits")
        assert send.call_args.kwargs["variant"] == "board"
        assert fake_db.store["doc1"]["board_request_id"] == "r1"

    @pytest.mark.asyncio
    async def test_daily_cap_falls_through_to_board(self, fake_db, monkeypatch):
        fake_db.store["doc1"] = _intake_doc()
        monkeypatch.setattr(khc, "_recent_duplicate_exists", lambda db, doc: False)
        monkeypatch.setattr(khc, "_jobs_today", lambda db: 3)  # at the cap
        monkeypatch.setattr(khc, "_community_version_url", AsyncMock(return_value=None))
        svc = _user_service(existing=True, credits=5)
        monkeypatch.setattr(khc, "get_user_service", lambda: svc)
        monkeypatch.setattr(khc, "get_settings", lambda: _settings(cap=3))
        board = AsyncMock(return_value={
            "status": "ok", "outcome": "board_submitted", "variant": "board",
            "board_request_id": "r1", "board_reason": "daily_cap"})
        monkeypatch.setattr(khc, "_submit_to_board", board)
        monkeypatch.setattr(khc, "_send_conversion_email", MagicMock(return_value=True))

        result = await khc.process_intake("doc1")

        assert result["status"] == "board_submitted"
        board.assert_awaited_once()
        assert board.call_args.args[3] == "daily_cap"

    @pytest.mark.asyncio
    async def test_recent_duplicate_is_dropped_without_side_effects(self, fake_db, monkeypatch):
        fake_db.store["doc1"] = _intake_doc()
        monkeypatch.setattr(khc, "_recent_duplicate_exists", lambda db, doc: True)
        svc = _user_service()
        monkeypatch.setattr(khc, "get_user_service", lambda: svc)
        send = MagicMock()
        monkeypatch.setattr(khc, "_send_conversion_email", send)

        result = await khc.process_intake("doc1")

        assert result["status"] == "duplicate"
        svc.get_or_create_user.assert_not_called()
        svc.add_credits.assert_not_called()
        send.assert_not_called()
        assert fake_db.store["doc1"]["outcome"] == "duplicate"

    @pytest.mark.asyncio
    async def test_terminal_doc_is_not_reprocessed_unless_forced(self, fake_db, monkeypatch):
        fake_db.store["doc1"] = _intake_doc(outcome="job_created")
        svc = _user_service()
        monkeypatch.setattr(khc, "get_user_service", lambda: svc)

        result = await khc.process_intake("doc1")

        assert result["status"] == "already_processed"
        svc.get_user.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_doc_never_processes(self, fake_db):
        fake_db.store["doc1"] = _intake_doc(outcome="invalid", email="junk")
        result = await khc.process_intake("doc1")
        assert result["status"] == "invalid"

    @pytest.mark.asyncio
    async def test_missing_doc(self, fake_db):
        result = await khc.process_intake("nope")
        assert result["status"] == "not_found"

    @pytest.mark.asyncio
    async def test_credit_grant_failure_is_error(self, fake_db, quiet_side_paths, monkeypatch):
        fake_db.store["doc1"] = _intake_doc()
        svc = _user_service(existing=False, add_ok=False)
        monkeypatch.setattr(khc, "get_user_service", lambda: svc)
        send = MagicMock()
        monkeypatch.setattr(khc, "_send_conversion_email", send)

        result = await khc.process_intake("doc1")

        assert result["status"] == "error"
        send.assert_not_called()
        assert fake_db.store["doc1"]["outcome"] == "error"

    @pytest.mark.asyncio
    async def test_parked_job_sends_pick_variant(self, fake_db, quiet_side_paths, monkeypatch):
        fake_db.store["doc1"] = _intake_doc()
        svc = _user_service(existing=False, credits=1)
        monkeypatch.setattr(khc, "get_user_service", lambda: svc)
        monkeypatch.setattr(khc, "get_settings", _settings)
        monkeypatch.setattr(khc, "_make_job", AsyncMock(return_value={
            "status": "ok", "outcome": "job_parked", "variant": "job_parked",
            "job_id": "j1"}))
        send = MagicMock(return_value=True)
        monkeypatch.setattr(khc, "_send_conversion_email", send)

        result = await khc.process_intake("doc1")

        assert result["status"] == "job_parked"
        assert send.call_args.kwargs["variant"] == "job_parked"

    @pytest.mark.asyncio
    async def test_board_rate_limited_is_error(self, fake_db, quiet_side_paths, monkeypatch):
        fake_db.store["doc1"] = _intake_doc()
        svc = _user_service(existing=True, credits=0)
        monkeypatch.setattr(khc, "get_user_service", lambda: svc)
        monkeypatch.setattr(khc, "get_settings", _settings)
        board_svc = MagicMock()
        board_svc.submit_request = AsyncMock(side_effect=SubmissionRateLimited())
        monkeypatch.setattr(khc, "get_song_request_service", lambda: board_svc)
        send = MagicMock()
        monkeypatch.setattr(khc, "_send_conversion_email", send)

        result = await khc.process_intake("doc1")

        assert result["status"] == "error"
        send.assert_not_called()


# ---------------------------------------------------------------- search/select

class TestSearchAndStart:
    def _job_manager(self, status=JobStatus.PENDING):
        jm = MagicMock()
        jm.get_job.return_value = SimpleNamespace(status=status)
        return jm

    @pytest.mark.asyncio
    async def test_unconfident_pick_parks_job(self, monkeypatch):
        jm = self._job_manager()
        monkeypatch.setattr(khc, "JobManager", lambda: jm)
        search = MagicMock()
        search.search_async = AsyncMock(return_value=[MagicMock()])
        monkeypatch.setattr(khc, "AudioSearchService", lambda: search)
        monkeypatch.setattr(khc, "pick_auto_selection", lambda results, search_title: None)
        with patch("backend.workers.bulk_search_worker._prepare_theme"):
            started = await khc._search_and_start("j1", "A", "T")

        assert started is False
        parked = [c for c in jm.transition_to_state.call_args_list
                  if c.kwargs.get("new_status") == JobStatus.AWAITING_AUDIO_SELECTION]
        assert parked, "job must be parked for owner selection"

    @pytest.mark.asyncio
    async def test_search_failure_parks_job(self, monkeypatch):
        jm = self._job_manager()
        monkeypatch.setattr(khc, "JobManager", lambda: jm)
        search = MagicMock()
        search.search_async = AsyncMock(side_effect=NoResultsError("nothing"))
        monkeypatch.setattr(khc, "AudioSearchService", lambda: search)
        with patch("backend.workers.bulk_search_worker._prepare_theme"):
            started = await khc._search_and_start("j1", "A", "T")

        assert started is False

    @pytest.mark.asyncio
    async def test_confident_pick_triggers_download(self, monkeypatch):
        jm = self._job_manager()
        monkeypatch.setattr(khc, "JobManager", lambda: jm)
        search = MagicMock()
        search.search_async = AsyncMock(return_value=[MagicMock()])
        search.last_remote_search_id = "rs1"
        monkeypatch.setattr(khc, "AudioSearchService", lambda: search)
        monkeypatch.setattr(khc, "pick_auto_selection", lambda results, search_title: 0)
        worker = MagicMock()
        worker.trigger_audio_download_worker = AsyncMock(return_value=True)
        monkeypatch.setattr(khc, "get_worker_service", lambda: worker)
        with patch("backend.workers.bulk_search_worker._prepare_theme"), \
             patch("backend.api.routes.audio_search._validate_and_prepare_selection") as validate:
            started = await khc._search_and_start("j1", "A", "T")

        assert started is True
        validate.assert_called_once_with(job_id="j1", selection_index=0)
        worker.trigger_audio_download_worker.assert_awaited_once_with("j1")

    @pytest.mark.asyncio
    async def test_already_progressed_job_is_left_alone(self, monkeypatch):
        jm = self._job_manager(status=JobStatus.DOWNLOADING_AUDIO)
        monkeypatch.setattr(khc, "JobManager", lambda: jm)

        started = await khc._search_and_start("j1", "A", "T")

        assert started is True
        jm.transition_to_state.assert_not_called()


# ---------------------------------------------------------------- email template

class TestConversionEmail:
    @pytest.mark.parametrize("variant,expect", [
        ("job", "Sign in & track your song"),
        ("job_parked", "Sign in & choose the recording"),
        ("board", "requests.nomadkaraoke.com"),
        ("community", "https://youtube.com/watch?v=x"),
        ("uninstall", "Please uninstall the KaraokeHunt app."),
    ])
    def test_variants_render_and_send(self, variant, expect):
        from backend.services.email_service import EmailService

        service = EmailService.__new__(EmailService)
        service.frontend_url = "https://gen.nomadkaraoke.com"
        sent = {}

        def capture(email, subject, html_content, text_content=None, **kw):
            sent.update(email=email, subject=subject, html=html_content,
                        text=text_content, **kw)
            return True

        service._log_and_send = capture
        ok = service.send_karaokehunt_conversion(
            email="fan@example.com", artist="Olivia <R>", title="good 4 u",
            variant=variant, login_url="https://gen.nomadkaraoke.com/auth/verify?token=tok",
            community_url="https://youtube.com/watch?v=x" if variant == "community" else None,
            is_new_user=(variant == "community"),
        )

        assert ok is True
        assert "good 4 u" in sent["subject"]
        assert expect in sent["html"]
        # Every variant carries a sign-in path (button or secondary link).
        assert "token=tok" in sent["html"]
        # User content must be HTML-escaped.
        assert "Olivia <R>" not in sent["html"]
        assert "Olivia &lt;R&gt;" in sent["html"]
        # First-time variants tell them to uninstall; the uninstall variant IS
        # that message and must not duplicate the one-time-conversion note.
        if variant == "uninstall":
            assert "one-time conversion" not in sent["html"]
        else:
            assert "one-time conversion" in sent["html"]
        if variant == "community":
            # The main button must be the YouTube link, plus a credit note.
            assert "free credit" in sent["html"]
        assert sent["email_type"] == "karaokehunt_conversion"
