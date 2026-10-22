"""Unit tests for the local-stack fake workers (backend/services/fake_workers)
and the FAKE_WORKERS seams in real services.

The end-to-end behaviour (a job walking PENDING -> COMPLETE on the emulators)
is covered by frontend/e2e/local-stack and backend/tests/emulator/test_fake_workers_pipeline.py.
"""
import asyncio
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services import fake_workers
from backend.services.fake_workers import config as fw_config
from backend.services.fake_workers.config import FakeConfig, get_fake_config, set_fake_config


# --------------------------------------------------------------------------- in-memory Firestore

class _Snap:
    def __init__(self, ref, data):
        self.reference, self.id, self._data = ref, ref.id, data

    @property
    def exists(self):
        return self._data is not None

    def to_dict(self):
        return dict(self._data) if self._data is not None else None


class _Doc:
    def __init__(self, store, coll, doc_id):
        self.store, self.coll, self.id = store, coll, doc_id

    def get(self):
        return _Snap(self, self.store.get(self.coll, {}).get(self.id))

    def set(self, data):
        self.store.setdefault(self.coll, {})[self.id] = dict(data)

    def update(self, data):
        self.store.setdefault(self.coll, {}).setdefault(self.id, {}).update(data)

    def delete(self):
        self.store.get(self.coll, {}).pop(self.id, None)


class _Coll:
    def __init__(self, store, name):
        self.store, self.name = store, name

    def document(self, doc_id):
        return _Doc(self.store, self.name, doc_id)

    def add(self, data):
        doc_id = f"auto{len(self.store.get(self.name, {}))}"
        self.document(doc_id).set(data)

    def stream(self):
        return [_Doc(self.store, self.name, k).get() for k in list(self.store.get(self.name, {}))]


class FakeDB:
    def __init__(self):
        self.store = {}

    def collection(self, name):
        return _Coll(self.store, name)


# --------------------------------------------------------------------------- the on/off switch

class TestFakeWorkersEnabled:
    def test_true_only_for_real_true(self):
        assert fake_workers.fake_workers_enabled(SimpleNamespace(fake_workers=True)) is True
        assert fake_workers.fake_workers_enabled(SimpleNamespace(fake_workers=False)) is False
        assert fake_workers.fake_workers_enabled(SimpleNamespace()) is False

    def test_magicmock_settings_are_off(self):
        # Many tests patch settings with a MagicMock; its truthy attributes must
        # never turn the fakes on.
        assert fake_workers.fake_workers_enabled(MagicMock()) is False

    def test_off_by_default_in_tests(self):
        assert fake_workers.fake_workers_enabled() is False


class TestProductionSafety:
    def test_startup_refuses_fake_workers_in_production(self, monkeypatch):
        from backend import config

        monkeypatch.setenv("ENVIRONMENT", "production")
        for var in config._REQUIRED_PRODUCTION_ENV_VARS:
            monkeypatch.setenv(var, "set")
        monkeypatch.setattr(config.settings, "fake_workers", True)
        with pytest.raises(RuntimeError, match="FAKE_WORKERS"):
            config.validate_production_config()

    def test_production_without_fake_workers_still_starts(self, monkeypatch):
        from backend import config

        monkeypatch.setenv("ENVIRONMENT", "production")
        for var in config._REQUIRED_PRODUCTION_ENV_VARS:
            monkeypatch.setenv(var, "set")
        monkeypatch.setattr(config.settings, "fake_workers", False)
        config.validate_production_config()

    def test_get_secret_never_calls_secret_manager_in_fake_mode(self, monkeypatch):
        from backend.config import Settings

        s = Settings()
        monkeypatch.setattr(s, "fake_workers", True)
        monkeypatch.delenv("SOME_LOCAL_SECRET", raising=False)
        with patch("google.cloud.secretmanager.SecretManagerServiceClient") as client:
            assert s.get_secret("some-local-secret") is None
        client.assert_not_called()

    def test_get_secret_env_var_still_wins_in_fake_mode(self, monkeypatch):
        from backend.config import Settings

        s = Settings()
        monkeypatch.setattr(s, "fake_workers", True)
        monkeypatch.setenv("SOME_ENV_SECRET", "from-env")
        assert s.get_secret("some-env-secret") == "from-env"

    def test_test_router_not_mounted_without_flag(self):
        from backend.main import app

        assert not [r for r in app.routes if getattr(r, "path", "").startswith("/api/test")]


# --------------------------------------------------------------------------- dispatch seam

class TestWorkerServiceSeam:
    KINDS = {
        "trigger_audio_download_worker": "audio_download",
        "trigger_audio_worker": "audio",
        "trigger_lyrics_worker": "lyrics",
        "trigger_screens_worker": "screens",
        "trigger_render_video_worker": "render_video",
        "trigger_video_worker": "video",
    }

    @pytest.mark.parametrize("method,kind", KINDS.items())
    def test_every_trigger_goes_to_the_fake_when_enabled(self, method, kind):
        from backend.services.worker_service import WorkerService

        ws = WorkerService()
        ws.settings = SimpleNamespace(fake_workers=True)
        with patch("backend.services.fake_workers.dispatch", return_value=True) as dispatch:
            assert asyncio.run(getattr(ws, method)("job123")) is True
        dispatch.assert_called_once_with(kind, "job123")

    def test_trigger_does_not_fake_when_disabled(self):
        from backend.services.worker_service import WorkerService

        ws = WorkerService()
        assert not fake_workers.fake_workers_enabled(ws.settings)
        with patch("backend.services.fake_workers.dispatch") as dispatch, \
                patch.object(ws, "trigger_worker", AsyncMock(return_value=True)):
            asyncio.run(ws.trigger_screens_worker("job123"))
        dispatch.assert_not_called()

    def test_dispatch_rejects_unknown_kind(self):
        with pytest.raises(ValueError):
            fake_workers.dispatch("nope", "job123")

    def test_dispatch_runs_the_runner_in_a_thread(self):
        done = []

        async def fake_run(kind, job_id):
            done.append((kind, job_id))
            return True

        with patch("backend.services.fake_workers.pipeline.run", fake_run):
            assert fake_workers.dispatch("lyrics", "abc") is True
            for t in list(fake_workers._threads):
                t.join(timeout=5)
        assert done == [("lyrics", "abc")]
        assert fake_workers.active_worker_count() == 0


class TestPipelineFailure:
    def test_fail_at_fails_the_job_like_a_crash(self):
        from backend.services.fake_workers import pipeline

        jm = MagicMock()
        jm.get_job.return_value = SimpleNamespace(user_email="u@example.com")
        with patch.object(pipeline, "JobManager", return_value=jm), \
                patch.object(pipeline, "get_fake_config",
                             return_value=FakeConfig(step_delay_seconds=0, fail_at="lyrics")):
            assert asyncio.run(pipeline.run("lyrics", "job1")) is False
        jm.fail_job.assert_called_once()
        args, kwargs = jm.fail_job.call_args
        assert args[0] == "job1"
        assert "lyrics" in args[1]
        assert kwargs["error_details"]["stage"] == "lyrics"

    def test_render_skips_when_not_review_complete(self):
        from backend.models.job import JobStatus
        from backend.services.fake_workers import pipeline

        jm = MagicMock()
        jm.get_job.return_value = SimpleNamespace(user_email="u@example.com", status=JobStatus.AWAITING_REVIEW)
        with patch.object(pipeline, "JobManager", return_value=jm), \
                patch.object(pipeline, "StorageService"), \
                patch.object(pipeline, "get_fake_config", return_value=FakeConfig(step_delay_seconds=0)):
            assert asyncio.run(pipeline.run("render_video", "job1")) is True
        jm.transition_to_state.assert_not_called()


# --------------------------------------------------------------------------- config

class TestFakeConfig:
    def test_defaults(self):
        c = FakeConfig()
        assert c.auto_approve is False and c.fail_at is None and c.audio_duration_seconds == 30

    @pytest.mark.parametrize("bad", [
        {"fail_at": "nope"},
        {"step_delay_seconds": -1},
        {"audio_duration_seconds": 2},
        {"search_result_count": 9},
    ])
    def test_validate_rejects(self, bad):
        with pytest.raises(ValueError):
            FakeConfig(**bad).validate()

    def test_per_user_overrides_layer_over_global(self):
        db = FakeDB()
        set_fake_config({"step_delay_seconds": 2.0, "auto_approve": True}, db=db)
        set_fake_config({"auto_approve": False}, db=db, email="A@Example.com")
        assert get_fake_config(db).auto_approve is True
        user = get_fake_config(db, email="a@example.com")
        assert user.auto_approve is False and user.step_delay_seconds == 2.0
        assert get_fake_config(db, email="other@example.com").auto_approve is True

    def test_unknown_keys_rejected(self):
        with pytest.raises(ValueError, match="Unknown"):
            set_fake_config({"bogus": 1}, db=FakeDB())

    def test_invalid_values_not_stored(self):
        db = FakeDB()
        with pytest.raises(ValueError):
            set_fake_config({"fail_at": "nope"}, db=db, email="a@example.com")
        assert get_fake_config(db, email="a@example.com").fail_at is None

    def test_clear_removes_global_and_user_overrides(self):
        db = FakeDB()
        set_fake_config({"auto_approve": True}, db=db)
        set_fake_config({"fail_at": "audio"}, db=db, email="a@example.com")
        fw_config.clear_fake_config(db)
        assert get_fake_config(db, email="a@example.com") == FakeConfig()


# --------------------------------------------------------------------------- canned data

class TestFakeLyrics:
    def test_corrections_round_trip_through_the_real_schema(self):
        from karaoke_gen.lyrics_transcriber.types import CorrectionResult
        from backend.services.fake_workers.lyrics import FAKE_LINES, build_corrections

        data = build_corrections("Artist", "Title", 30)
        result = CorrectionResult.from_dict(data)
        assert len(result.corrected_segments) == len(FAKE_LINES)
        words = [w for s in result.corrected_segments for w in s.words]
        assert all(0 < w.start_time < w.end_time <= 30 for w in words)
        assert [w.start_time for w in words] == sorted(w.start_time for w in words)
        ref = result.reference_lyrics["lrclib"]
        assert ref.metadata.is_synced and ref.metadata.track_name == "Title"
        assert [s.text for s in ref.segments] == [s.text for s in result.corrected_segments]

    def test_lrc(self):
        from backend.services.fake_workers.lyrics import build_corrections, build_lrc

        lrc = build_lrc(build_corrections("A", "T", 30))
        assert lrc.startswith("[00:02.00]This is a fake song for testing")


class TestFakeSearch:
    def _search(self, count=3):
        from backend.services.fake_workers.search import FakeAudioSearchService

        with patch("backend.services.fake_workers.config.get_fake_config",
                   return_value=FakeConfig(search_result_count=count, audio_duration_seconds=42)):
            return FakeAudioSearchService().search("Artist", "Song")

    def test_results_in_the_shapes_the_ui_ranks(self):
        results = self._search()
        assert [r.provider for r in results] == ["RED", "Spotify", "YouTube"]
        assert [r.index for r in results] == [0, 1, 2]
        best = results[0].to_dict()
        assert best["is_lossless"] is True and best["seeders"] >= 50 and best["duration"] == 42

    def test_zero_results_is_no_results(self):
        from karaoke_gen.audio_fetcher import NoResultsError

        with pytest.raises(NoResultsError):
            self._search(count=0)

    def test_factory_returns_fake_when_enabled(self, monkeypatch):
        from backend.services import audio_search_service as svc
        from backend.services.fake_workers.search import FakeAudioSearchService

        monkeypatch.setattr(svc, "_audio_search_service", None)
        monkeypatch.setattr(svc, "get_settings", lambda: SimpleNamespace(fake_workers=True))
        assert isinstance(svc.get_audio_search_service(), FakeAudioSearchService)
        monkeypatch.setattr(svc, "_audio_search_service", None)


class TestOutbox:
    def test_codes_and_links_from_html_only_email(self):
        from backend.services.fake_workers import outbox

        db = FakeDB()
        html = (
            "<style>.x{color:#123456;width:100000px}</style>"
            "<p>Your code is <b>482915</b></p>"
            '<a href="http://127.0.0.1:3010/en/auth/verify?token=abc&amp;ref=x">Sign in</a>'
        )
        with patch.object(outbox, "_db", return_value=db):
            outbox.record_email("User@Example.com", "Your code", html, None)
            emails = outbox.list_emails("user@example.com")
        assert emails[0]["codes"] == ["482915"]
        assert emails[0]["links"] == ["http://127.0.0.1:3010/en/auth/verify?token=abc&ref=x"]

    def test_filter_and_newest_first(self):
        from datetime import datetime, timedelta, timezone

        from backend.services.fake_workers import outbox

        db = FakeDB()
        now = datetime.now(timezone.utc)
        with patch.object(outbox, "_db", return_value=db):
            for i, to in enumerate(["a@x.com", "b@x.com", "a@x.com"]):
                db.collection(outbox.OUTBOX_COLLECTION).document(f"d{i}").set(
                    {"to": to, "subject": f"s{i}", "created_at": now + timedelta(seconds=i)})
            assert [e["subject"] for e in outbox.list_emails("a@x.com")] == ["s2", "s0"]

    def test_capture_failure_never_breaks_a_send(self):
        from backend.services.fake_workers import outbox

        with patch.object(outbox, "_db", side_effect=RuntimeError("emulator down")):
            outbox.record_email("a@x.com", "s", "<p>hi</p>", None)  # no raise


class TestFakeCheckout:
    def test_session_and_redirects(self, monkeypatch):
        from backend.services.fake_workers import checkout

        db = FakeDB()
        monkeypatch.setenv("LOCAL_STACK_API_URL", "http://10.0.2.2:8010/")
        with patch.object(checkout, "_db", return_value=db):
            session = checkout.create_fake_checkout({
                "customer_email": "a@x.com",
                "metadata": {"package_id": "3_credits", "credits": "3"},
                "success_url": "http://web/payment/success?session_id={CHECKOUT_SESSION_ID}",
                "line_items": [{"price_data": {"unit_amount": 2400, "product_data": {"name": "3 Karaoke Credits"}},
                                "quantity": 1}],
            })
            record = checkout.get_checkout(session.id)
        assert session.id.startswith("e2e-test-")  # accepted by the shared test-webhook handler
        assert session.url == f"http://10.0.2.2:8010/api/test/fake-checkout/{session.id}"
        assert record["amount_cents"] == 2400 and record["product_name"] == "3 Karaoke Credits"
        assert checkout.resolve_url(record["success_url"], session.id).endswith(f"session_id={session.id}")

    def test_stripe_service_uses_fake_only_when_unconfigured_and_enabled(self):
        from backend.services.stripe_service import StripeService

        svc = StripeService()
        svc.secret_key = None
        with patch("backend.services.stripe_service.fake_workers_enabled", return_value=False):
            assert svc.can_checkout() is False
            ok, url, _ = svc.create_checkout_session("1_credit", "a@x.com")
            assert ok is False and url is None
        with patch("backend.services.stripe_service.fake_workers_enabled", return_value=True), \
                patch("backend.services.fake_workers.checkout.create_fake_checkout",
                      return_value=SimpleNamespace(id="e2e-test-local-1", url="http://api/x")) as fake:
            assert svc.can_checkout() is True
            ok, url, _ = svc.create_checkout_session("1_credit", "a@x.com")
        assert ok is True and url == "http://api/x"
        assert fake.call_args[0][0]["metadata"]["credits"] == "1"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
class TestFakeMedia:
    def test_builds_real_files_of_the_requested_length(self):
        from backend.services.fake_workers.media import get_media

        media = get_media(5)
        assert get_media(5) is media  # cached
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", media.input_audio],
            capture_output=True, text=True, check=True,
        )
        assert abs(float(out.stdout) - 5) < 0.5
        for path in (media.vocals, media.backing_vocals, media.title_png, media.final_mp4, media.cdg_zip):
            assert open(path, "rb").read(4)


# --------------------------------------------------------------------------- mobile-app knobs

class TestSummaryRefundFlag:
    def test_summary_includes_credit_refunded(self):
        # Top-level Job field (set by job_manager / stale-review / duration refunds),
        # so only the Firestore projection needs it. The mobile app shows "refunded"
        # on failed jobs from the summary list.
        from backend.services.firestore_service import FirestoreService

        assert 'credit_refunded' in FirestoreService.SUMMARY_FIELD_PATHS


class TestFakeVideoYoutubeUrl:
    def _run(self, config, is_private=False):
        from backend.services.fake_workers import pipeline

        jm = MagicMock()
        jm.get_job.return_value = SimpleNamespace(user_email="u@example.com", is_private=is_private)
        media = SimpleNamespace(final_mp4="/tmp/x.mp4", final_mkv="/tmp/x.mkv",
                                cdg_zip="/tmp/c.zip", txt_zip="/tmp/t.zip")
        with patch.object(pipeline, "JobManager", return_value=jm), \
                patch.object(pipeline, "StorageService"), \
                patch.object(pipeline, "_media_for", return_value=media), \
                patch.object(pipeline, "get_fake_config", return_value=config):
            assert asyncio.run(pipeline.run("video", "job1")) is True
        return {c.args[1]: c.args[2] for c in jm.update_state_data.call_args_list}

    def test_fake_video_sets_youtube_url_when_public(self):
        from backend.services.fake_workers.pipeline import FAKE_YOUTUBE_URL

        state = self._run(FakeConfig(step_delay_seconds=0))
        assert state["youtube_url"] == FAKE_YOUTUBE_URL == "https://youtu.be/dQw4w9WgXcQ"

    def test_fake_video_no_youtube_url_when_private(self):
        assert "youtube_url" not in self._run(FakeConfig(step_delay_seconds=0), is_private=True)

    def test_fake_video_no_youtube_url_when_disabled(self):
        assert "youtube_url" not in self._run(FakeConfig(step_delay_seconds=0, youtube_url_enabled=False))


class TestFakeSearchConfidence:
    """``search_confidence`` shapes the fake results into the web UI's tiers.

    The frontend test ``lib/__tests__/fake-search-tiers.test.ts`` runs the real
    ``getSearchConfidence`` over ``FRONTEND_FIXTURE`` (generated from these same
    shapes), so the fake and the ranking can't drift apart.
    """

    def _search(self, confidence, count=3):
        from backend.services.fake_workers.search import FakeAudioSearchService

        with patch("backend.services.fake_workers.config.get_fake_config",
                   return_value=FakeConfig(search_confidence=confidence, search_result_count=count)):
            return [r.to_dict() for r in FakeAudioSearchService().search("Artist", "Song")]

    def test_defaults(self):
        c = FakeConfig()
        assert c.search_confidence == "perfect" and c.youtube_url_enabled is True

    def test_validate_rejects_unknown_confidence(self):
        with pytest.raises(ValueError):
            FakeConfig(search_confidence="maybe").validate()

    def test_fake_search_confidence_shapes(self):
        from backend.services.audio_search_service import BEST_CHOICE_MIN_SEEDERS

        perfect = self._search("perfect")
        assert perfect[0]["seeders"] >= 50 and perfect[0]["target_file"] == "01 - Song.flac"

        recommended = self._search("recommended")
        # Lossless and the right track, but not well seeded enough for "BEST CHOICE".
        assert recommended[0]["is_lossless"] is True and 0 < recommended[0]["seeders"] < BEST_CHOICE_MIN_SEEDERS
        assert recommended[0]["target_file"] == "01 - Song.flac"

        unsure = self._search("unsure")
        # Wrong track names everywhere and almost no seeders.
        assert unsure[0]["seeders"] < 10
        assert all("Song" not in (r.get("target_file") or r["title"]) for r in unsure)
        assert [r["provider"] for r in unsure] == ["RED", "Spotify", "YouTube"]

    def test_result_count_still_applies(self):
        assert len(self._search("unsure", count=1)) == 1

    def test_frontend_fixture_is_current(self):
        """Regenerate with:
        poetry run python -m backend.services.fake_workers.search --write-fixture
        """
        import json

        from backend.services.fake_workers.search import FRONTEND_FIXTURE, frontend_fixture

        with open(FRONTEND_FIXTURE) as f:
            assert json.load(f) == frontend_fixture()


# --------------------------------------------------------------------------- kjbox quick version

class TestKjboxQuickVersion:
    def _run_audio(self, request_metadata):
        from backend.services.fake_workers import pipeline

        jm = MagicMock()
        jm.get_job.return_value = SimpleNamespace(
            job_id="job1", user_email="u@example.com", artist="A", title="T",
            request_metadata=request_metadata, state_data={},
        )
        jm.advance_to_screens_if_ready = AsyncMock()
        jm.resume_video_if_waiting_for_stems = AsyncMock()
        storage = MagicMock()
        media = MagicMock(final_mp4=__file__, duration=30)
        with patch.object(pipeline, "JobManager", return_value=jm), \
                patch.object(pipeline, "StorageService", return_value=storage), \
                patch.object(pipeline, "_media_for", return_value=media), \
                patch.object(pipeline, "get_fake_config", return_value=FakeConfig(step_delay_seconds=0)), \
                patch("backend.workers.audio_worker.upload_separation_results", AsyncMock()), \
                patch("backend.workers.audio_worker._transcode_review_stems", AsyncMock()), \
                patch("backend.workers.screens_worker._analyze_backing_vocals", AsyncMock()), \
                patch("backend.workers.worker_logging.create_job_logger"):
            assert asyncio.run(pipeline.run("audio", "job1")) is True
        return jm, storage

    def test_fake_workers_emit_quick_version_for_kjbox_jobs(self):
        jm, storage = self._run_audio({"client_id": "kjbox"})
        jm.update_file_url.assert_called_once_with("job1", "quick", "video_mp4", "jobs/job1/quick/quick.mp4")
        storage.upload_file.assert_called_once()
        statuses = [c.args[2]["status"] for c in jm.update_state_data.call_args_list
                    if c.args[1] == "quick_version"]
        assert statuses == ["separating", "rendering", "ready"]

    def test_non_kjbox_jobs_get_no_quick_version(self):
        jm, storage = self._run_audio({"client_id": "web"})
        jm.update_file_url.assert_not_called()
        assert not [c for c in jm.update_state_data.call_args_list if c.args[1] == "quick_version"]
