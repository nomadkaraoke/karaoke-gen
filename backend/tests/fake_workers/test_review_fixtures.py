"""Fixtures and knobs the fake workers offer the mobile review flows (FakeConfig.review_fixture etc.)."""
import asyncio
import re
import shutil
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.models.job import JobStatus
from backend.services.fake_workers import config as fw_config
from backend.services.fake_workers.config import FakeConfig, get_fake_config, set_fake_config
from backend.services.fake_workers.lyrics import build_corrections
from backend.tests.test_fake_workers import FakeDB

HEBREW = re.compile("[֐-׿]")
ARABIC = re.compile("[؀-ۿ]")


def _words(doc):
    return [w for s in doc["corrected_segments"] for w in s["words"]]


class TestFixtures:
    def test_fixture_corrections_shape(self):
        from karaoke_gen.lyrics_transcriber.types import CorrectionResult

        doc = build_corrections("A", "T", 30, "corrections")
        CorrectionResult.from_dict(doc)  # still the real schema

        words = _words(doc)
        replaced = [w for w in words if w.get("ai_corrected") and w.get("original_text")]
        assert replaced
        assert any(w.get("timing_estimated") for w in words)
        assert [w for w in words if w["start_time"] is None] and \
            len([w for w in words if w["start_time"] is None]) == 1
        assert any(w["start_time"] is not None and w["end_time"] - w["start_time"] > 2 for w in words)

        gap = doc["vocal_gaps"]["gaps"][0]
        assert gap["evidenced"] is True
        assert gap["synced_reference_lines"]["lrclib"]

        undo = doc["metadata"]["auto_approval"]["undo_info"]
        assert undo["fake-sug-replace"]["removed_words"][0]["text"] == "arrive"
        assert undo["fake-sug-replace"]["new_word_ids"] == ["fake-ai-arrives"]
        # every undo target exists in the served segments, and the heard text is in original_segments
        ids = {w["id"] for w in words}
        assert all(set(u["new_word_ids"]) <= ids for u in undo.values())
        assert "arrive" in [w["text"] for s in doc["original_segments"] for w in s["words"]]

    def test_default_fixture_is_unchanged(self):
        doc = build_corrections("A", "T", 30)
        assert "vocal_gaps" not in doc and "is_duet" not in doc
        assert all(s.get("singer") is None for s in doc["corrected_segments"])

    def test_fixture_duet(self):
        doc = build_corrections("A", "T", 30, "duet")
        assert doc["is_duet"] is True
        assert {s["singer"] for s in doc["corrected_segments"]} == {0, 1, 2}

    def test_fixture_rtl(self):
        doc = build_corrections("A", "T", 30, "rtl")
        texts = [s["text"] for s in doc["corrected_segments"]]
        assert all(HEBREW.search(t) for t in texts)
        assert sum(1 for t in texts if ARABIC.search(t)) == 1

    def test_fixture_large(self):
        from karaoke_gen.lyrics_transcriber.types import CorrectionResult

        doc = build_corrections("A", "T", 30, "large")
        assert len(doc["corrected_segments"]) == 300
        words = _words(doc)
        assert len(words) >= 2000
        starts = [w["start_time"] for w in words]
        assert starts == sorted(starts) and len(set(starts)) == len(starts)
        assert all(w["start_time"] < w["end_time"] for w in words)
        CorrectionResult.from_dict(doc)


class TestKnobs:
    def test_defaults_keep_todays_behaviour(self):
        c = FakeConfig()
        assert (c.review_fixture, c.backing_vocals, c.stems_delay_seconds) == ("default", "silent", 0)
        assert (c.review_mode, c.auto_approval, c.review_complete_fail_once) == ("always_review", "none", False)

    @pytest.mark.parametrize("bad", [
        {"review_fixture": "nope"}, {"backing_vocals": "loud"}, {"stems_delay_seconds": -1},
        {"stems_delay_seconds": 601}, {"stems_delay_seconds": "2"}, {"review_mode": "manual"},
        {"auto_approval": "yes"}, {"review_complete_fail_once": "true"},
    ])
    def test_validate_rejects(self, bad):
        with pytest.raises(ValueError):
            set_fake_config(bad, db=FakeDB(), email="u@example.com")

    def test_consume_review_complete_failure_is_one_shot_per_user(self):
        db = FakeDB()
        set_fake_config({"review_complete_fail_once": True}, db=db, email="a@example.com")
        assert fw_config.consume_review_complete_failure("b@example.com", db=db) is False
        assert fw_config.consume_review_complete_failure("a@example.com", db=db) is True
        assert fw_config.consume_review_complete_failure("a@example.com", db=db) is False


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
class TestBackingVocals:
    def test_backing_audible_recommends_backing(self, tmp_path):
        from backend.services.fake_workers.media import get_media
        from backend.workers.screens_worker import _analyze_backing_vocals

        media = get_media(30, "audible")
        assert get_media(30, "silent").backing_vocals != media.backing_vocals

        def analyse(stem):
            storage = MagicMock()
            storage.download_file.side_effect = lambda _src, dst: shutil.copy(stem, dst)
            jm = MagicMock()
            jm.get_job.return_value = SimpleNamespace(file_urls={"stems": {"backing_vocals": "stems/b.flac"}})
            with patch("backend.services.audio_analysis_service.StorageService", return_value=storage):
                asyncio.run(_analyze_backing_vocals("job1", jm, storage, MagicMock()))
            calls = [c for c in jm.update_state_data.call_args_list if c[0][1] == "backing_vocals_analysis"]
            return calls[-1][0][2]

        audible = analyse(media.backing_vocals)
        assert audible["has_audible_content"] is True
        assert audible["recommended_selection"] in ("with_backing", "review_needed")
        assert analyse(get_media(30, "silent").backing_vocals)["has_audible_content"] is False


class TestPipelineKnobs:
    def test_auto_approval_payloads_drive_the_review_summary(self):
        from backend.services.auto_approval.instrumental import auto_approval_summary
        from backend.services.fake_workers.pipeline import auto_approval_payload

        settings = SimpleNamespace(auto_approval_backing_keep_enabled=False, auto_approval_timing_gate_enabled=True)

        def summary(kind, review_mode="auto"):
            job = SimpleNamespace(
                processing_metadata={"auto_approval": auto_approval_payload(kind)}, review_mode=review_mode,
                backing_preference="auto", existing_instrumental_gcs_path=None, state_data={},
                file_urls={"stems": {"instrumental_clean": "x"}},
            )
            return auto_approval_summary(job, settings)

        assert auto_approval_payload("none") is None
        assert summary("lyrics_confident")["lyrics"]["confident"] is True
        assert summary("lyrics_confident")["backing"]["confident"] is False
        assert summary("backing_confident")["backing"]["resolved_selection"] == "clean"
        assert summary("backing_confident")["lyrics"]["confident"] is False
        both = summary("both_unsure")
        assert both["lyrics"]["confident"] is False and both["backing"]["confident"] is False
        assert summary("lyrics_confident", "always_review")["lyrics"]["confident"] is False

    def test_auto_approval_knob_writes_summary(self):
        from backend.services.fake_workers import pipeline

        jm = MagicMock()
        job = SimpleNamespace(user_email="u@example.com", status=JobStatus.LYRICS_COMPLETE, review_mode="auto")
        jm.get_job.return_value = job
        cfg = FakeConfig(step_delay_seconds=0, auto_approval="lyrics_confident", review_mode="auto")
        with patch.object(pipeline, "JobManager", return_value=jm), \
                patch.object(pipeline, "StorageService"), \
                patch.object(pipeline, "get_fake_config", return_value=cfg), \
                patch.object(pipeline, "get_media", return_value=MagicMock()), \
                patch("builtins.open", MagicMock()), \
                patch("backend.workers.screens_worker._upload_screens", AsyncMock()), \
                patch("backend.workers.screens_worker._transcode_review_audio", AsyncMock()):
            assert asyncio.run(pipeline.run_screens("job1")) is True
        writes = [c[0] for c in jm.update_processing_metadata.call_args_list]
        assert writes[0][1] == "auto_approval"
        assert writes[0][2]["lyrics"]["verdict"] == "auto"
        # parked for a human, not auto-completed
        assert jm.transition_to_state.call_args_list[-1][1]["new_status"] == JobStatus.AWAITING_REVIEW

    def test_stems_delay(self):
        from backend.services.fake_workers import pipeline

        order = []
        jm = MagicMock()
        jm.get_job.return_value = SimpleNamespace(user_email="u@example.com")
        jm.mark_audio_complete.side_effect = lambda *_: order.append("audio_complete")
        jm.advance_to_screens_if_ready = AsyncMock()
        jm.resume_video_if_waiting_for_stems = AsyncMock()

        async def upload(*_a, **_k):
            order.append("upload")

        async def sleep(seconds):
            order.append(("sleep", seconds))

        cfg = FakeConfig(step_delay_seconds=0, stems_delay_seconds=2)
        with patch.object(pipeline, "JobManager", return_value=jm), \
                patch.object(pipeline, "StorageService"), \
                patch.object(pipeline, "get_fake_config", return_value=cfg), \
                patch.object(pipeline, "get_media", return_value=MagicMock()), \
                patch.object(pipeline.asyncio, "sleep", sleep), \
                patch("backend.workers.audio_worker.upload_separation_results", upload), \
                patch("backend.workers.audio_worker._transcode_review_stems", AsyncMock()), \
                patch("backend.workers.screens_worker._analyze_backing_vocals", AsyncMock()):
            assert asyncio.run(pipeline.run_audio("job1")) is True
        assert order == [("sleep", 2), "upload", "audio_complete"]

    def test_audio_worker_does_not_block_other_fake_workers(self):
        """Each dispatched fake runs on its own thread, so one worker's delay never holds up the others."""
        from backend.services import fake_workers

        seen = []
        started = threading.Event()

        async def fake_run(kind, job_id):
            if kind == "audio":
                started.set()
                await asyncio.sleep(0.3)
            seen.append(kind)
            return True

        with patch("backend.services.fake_workers.pipeline.run", fake_run):
            fake_workers.dispatch("audio", "j")
            assert started.wait(2)
            fake_workers.dispatch("lyrics", "j")
            for t in list(fake_workers._threads):
                t.join(timeout=5)
        assert seen == ["lyrics", "audio"]


class TestLyricsWorkerStoresGaps:
    def test_vocal_gaps_go_to_state_data_not_corrections_json(self):
        from backend.services.fake_workers import pipeline

        jm, uploaded = MagicMock(), {}
        jm.get_job.return_value = SimpleNamespace(user_email="u@example.com", artist="A", title="T")
        jm.advance_to_screens_if_ready = AsyncMock()
        storage = MagicMock()

        def upload(path, dest):
            import json
            if dest.endswith("corrections.json"):
                uploaded.update(json.load(open(path)))
            return dest

        storage.upload_file.side_effect = upload
        cfg = FakeConfig(step_delay_seconds=0, review_fixture="corrections")
        with patch.object(pipeline, "JobManager", return_value=jm), \
                patch.object(pipeline, "StorageService", return_value=storage), \
                patch.object(pipeline, "get_fake_config", return_value=cfg):
            assert asyncio.run(pipeline.run_lyrics("job1")) is True
        assert "vocal_gaps" not in uploaded
        keys = {c[0][1] for c in jm.update_state_data.call_args_list}
        assert "vocal_gaps" in keys


# --------------------------------------------------------------------------- routes

@pytest.fixture
def review_client():
    from backend.api.dependencies import require_review_auth
    from backend.main import app

    async def allow():
        return ("job1", "full")

    app.dependency_overrides[require_review_auth] = allow
    yield TestClient(app)
    app.dependency_overrides.pop(require_review_auth, None)


class TestReviewRoutes:
    def test_complete_fail_once(self, review_client):
        db = FakeDB()
        set_fake_config({"review_complete_fail_once": True}, db=db, email="u@example.com")
        jm = MagicMock()
        jm.get_job.return_value = SimpleNamespace(user_email="u@example.com")
        ws = MagicMock()
        ws.trigger_render_video_worker = AsyncMock(return_value=True)
        with patch("backend.api.routes.review.JobManager", return_value=jm), \
                patch("backend.services.fake_workers.fake_workers_enabled", return_value=True), \
                patch("backend.services.fake_workers.config._default_db", return_value=db), \
                patch("backend.api.routes.review._save_review_completion", return_value="clean"), \
                patch("backend.services.worker_service.get_worker_service", return_value=ws):
            body = {"instrumental_selection": "clean"}
            assert review_client.post("/api/review/job1/complete", json=body).status_code == 503
            second = review_client.post("/api/review/job1/complete", json=body)
        assert second.status_code == 200
        assert second.json()["instrumental_selection"] == "clean"

    def test_complete_hook_inert_without_fake_workers(self, review_client):
        db = FakeDB()
        set_fake_config({"review_complete_fail_once": True}, db=db, email="u@example.com")
        ws = MagicMock()
        ws.trigger_render_video_worker = AsyncMock(return_value=True)
        with patch("backend.services.fake_workers.config._default_db", return_value=db), \
                patch("backend.api.routes.review._save_review_completion", return_value="clean"), \
                patch("backend.services.worker_service.get_worker_service", return_value=ws):
            resp = review_client.post("/api/review/job1/complete", json={"instrumental_selection": "clean"})
        assert resp.status_code == 200
        assert get_fake_config(db, email="u@example.com").review_complete_fail_once is True

    def test_audio_range(self, review_client):
        data = bytes(range(256)) * 4
        jm = MagicMock()
        jm.get_job.return_value = SimpleNamespace(input_media_gcs_path="jobs/job1/input.flac")
        transcoding = MagicMock()
        transcoding.get_review_audio_bytes_async = AsyncMock(return_value=(data, "audio/ogg"))
        with patch("backend.api.routes.review.JobManager", return_value=jm), \
                patch("backend.api.routes.review._dev_audio_proxy_enabled", return_value=True), \
                patch("backend.services.audio_transcoding_service.AudioTranscodingService",
                      return_value=transcoding):
            ranged = review_client.get("/api/review/job1/audio", headers={"Range": "bytes=0-99"})
            full = review_client.get("/api/review/job1/audio")
        assert ranged.status_code == 206
        assert ranged.content == data[:100]
        assert ranged.headers["content-range"] == f"bytes 0-99/{len(data)}"
        assert full.status_code == 200 and full.headers["accept-ranges"] == "bytes"


class TestReviewResultEndpoint:
    @pytest.fixture
    def client(self):
        from backend.api.routes import test_support

        app = FastAPI()
        app.include_router(test_support.router, prefix="/api")
        return TestClient(app)

    def _job(self, **state):
        return SimpleNamespace(status=JobStatus.REVIEW_COMPLETE, state_data=state, file_urls={})

    def test_returns_submitted_review(self, client):
        saved = {"corrected_segments": [
            {"text": "Hi there", "singer": 2, "words": [
                {"id": "a", "text": "Hi", "start_time": 1.0, "end_time": 1.5},
                {"id": "b", "text": "there", "start_time": 1.6, "end_time": 2.0},
            ]},
        ]}
        jm, storage = MagicMock(), MagicMock()
        jm.get_job.return_value = self._job(instrumental_selection="with_backing", is_duet=True)
        storage.file_exists.return_value = True
        storage.download_json.return_value = saved
        with patch("backend.services.job_manager.JobManager", return_value=jm), \
                patch("backend.services.storage_service.StorageService", return_value=storage):
            resp = client.get("/api/test/jobs/job1/review-result")
        assert resp.status_code == 200
        assert resp.json() == {
            "status": "review_complete", "instrumental_selection": "with_backing", "is_duet": True,
            "lines": [{"text": "Hi there", "singer": 2, "words": [
                {"text": "Hi", "start_time": 1.0, "end_time": 1.5},
                {"text": "there", "start_time": 1.6, "end_time": 2.0},
            ]}],
        }
        storage.file_exists.assert_called_with("jobs/job1/lyrics/corrections_updated.json")

    def test_before_a_review_lines_are_empty(self, client):
        jm, storage = MagicMock(), MagicMock()
        jm.get_job.return_value = self._job()
        storage.file_exists.return_value = False
        with patch("backend.services.job_manager.JobManager", return_value=jm), \
                patch("backend.services.storage_service.StorageService", return_value=storage):
            body = client.get("/api/test/jobs/job1/review-result").json()
        assert body["lines"] == [] and body["is_duet"] is False and body["instrumental_selection"] is None

    def test_unknown_job_404(self, client):
        jm = MagicMock()
        jm.get_job.return_value = None
        with patch("backend.services.job_manager.JobManager", return_value=jm):
            assert client.get("/api/test/jobs/nope/review-result").status_code == 404
