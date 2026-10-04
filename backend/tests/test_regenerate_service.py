"""
Tests for the storage-retention "Regenerate video" flow.

Covers backend/services/regenerate_service.py (validation, atomic claim, marker,
rate limits, trigger/failure handling), the stems-restore gate
(backend/services/stems_restore.py) and the audio worker's restore hand-off,
the GCS-only video pipeline (orchestrator + video worker completion), the
customer/admin endpoints, the archived-download 410, the internal retention
endpoint guards, the public->private visibility chain and the edit/retry
handling of stems-purged jobs.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from google.cloud.firestore_v1 import DELETE_FIELD

from backend.models.job import JobStatus
from backend.services import regenerate_service as rs
from backend.services.regenerate_service import (
    RegenerateService,
    active_regenerate,
    check_and_record_rate_limit,
    clear_regenerate_update,
    is_gcs_only_run,
    renders_missing,
    stems_need_restore,
    validate_regenerate,
)
from backend.services.theme_rerender_service import RerenderConflictError, RerenderError, rerender_brand_code

PURGED = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _job(**overrides):
    fields = dict(
        job_id="job123",
        status="complete",
        tenant_id=None,
        user_email="customer@example.com",
        artist="Artist",
        title="Title",
        is_private=False,
        outputs_deleted_at=None,
        prep_only=False,
        finalise_only=False,
        review_token="tok-1",
        input_media_gcs_path="jobs/job123/input/song.flac",
        existing_instrumental_gcs_path=None,
        theme_id="nomad",
        renders_purged_at=PURGED,
        stems_purged_at=PURGED,
        state_data={
            "instrumental_selection": "with_backing",
            "brand_code": "NOMAD-1234",
            "youtube_url": "https://www.youtube.com/watch?v=abc",
        },
        file_urls={
            "lyrics": {"corrections": "jobs/job123/lyrics/corrections.json"},
            "finals": {"lossy_720p_mp4": "jobs/job123/finals/lossy_720p_mp4.mp4"},
        },
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


# --- helpers ------------------------------------------------------------------------

class TestHelpers:
    def test_renders_missing(self):
        assert renders_missing(_job())
        full = {"lossy_4k_mp4": "a", "lossy_720p_mp4": "b"}
        assert not renders_missing(_job(renders_purged_at=None, file_urls={"finals": full}))
        assert renders_missing(_job(renders_purged_at=None, file_urls={"finals": {"lossy_720p_mp4": "b"}}))
        assert renders_missing(_job(renders_purged_at=None, file_urls={}))

    def test_stems_need_restore(self):
        assert stems_need_restore(_job())
        assert not stems_need_restore(_job(stems_purged_at=None))
        assert not stems_need_restore(_job(existing_instrumental_gcs_path="jobs/job123/custom_instrumental.wav"))
        assert not stems_need_restore(_job(finalise_only=True))

    def test_marker_is_scoped_to_review_token(self):
        job = _job(state_data={"regenerate": {"review_token": "tok-1", "brand_code": "NOMAD-1"}})
        assert active_regenerate(job) and is_gcs_only_run(job)
        assert rerender_brand_code(job) == "NOMAD-1"
        stale = _job(review_token="tok-2", state_data={"regenerate": {"review_token": "tok-1"}})
        assert active_regenerate(stale) == {} and not is_gcs_only_run(stale)
        assert clear_regenerate_update(stale) == {"state_data.regenerate": DELETE_FIELD}
        assert clear_regenerate_update(_job()) == {}

    def test_admin_marker_clear_also_drops_regenerate(self):
        from backend.services.admin_rerender_service import clear_admin_rerender_update
        job = _job(state_data={"regenerate": {"review_token": "tok-1"}})
        assert clear_admin_rerender_update(job) == {"state_data.regenerate": DELETE_FIELD}

    def test_notification_suppression(self):
        from backend.services.admin_rerender_service import suppress_customer_notifications
        quiet = _job(state_data={"regenerate": {"review_token": "tok-1", "notify_customer": False}})
        loud = _job(state_data={"regenerate": {"review_token": "tok-1", "notify_customer": True}})
        assert suppress_customer_notifications(quiet)
        assert not suppress_customer_notifications(loud)
        assert not suppress_customer_notifications(_job())


class TestValidate:
    def test_valid(self):
        assert validate_regenerate(_job()) is None

    @pytest.mark.parametrize("overrides, fragment", [
        ({"status": "in_review"}, "Only finished tracks"),
        ({"status": "failed"}, "Only finished tracks"),
        ({"outputs_deleted_at": "2026-09-29"}, "outputs were deleted"),
        ({"prep_only": True}, "can't be regenerated"),
        ({"finalise_only": True}, "can't be regenerated"),
        ({"state_data": {"instrumental_selection": "clean", "visibility_change_in_progress": True}}, "visibility change"),
        ({"state_data": {"instrumental_selection": "clean", "admin_rerender": {"brand_code": "X"}}}, "already being re-rendered"),
        ({"state_data": {"instrumental_selection": "clean", "theme_rerender": {"theme_id": "t"}}}, "already being re-rendered"),
        ({"state_data": {"instrumental_selection": "clean",
                         "storage_purge_in_progress": datetime.now(timezone.utc).isoformat()}}, "being archived"),
        ({"state_data": {}}, "instrumental selection"),
        ({"file_urls": {}}, "reviewed lyrics"),
        ({"input_media_gcs_path": None}, "original audio"),
        ({"theme_id": None}, "video style"),
    ])
    def test_rejections(self, overrides, fragment):
        assert fragment in validate_regenerate(_job(**overrides))

    def test_failed_regenerate_can_resume(self):
        job = _job(status="failed", state_data={"instrumental_selection": "clean",
                                                 "regenerate": {"review_token": "tok-1"}})
        assert validate_regenerate(job) is None

    def test_change_to_private_chain_allowed_during_visibility_change(self):
        job = _job(state_data={"instrumental_selection": "clean", "visibility_change_in_progress": True})
        assert validate_regenerate(job, after="change_to_private") is None


# --- service ------------------------------------------------------------------------

def _service(claim_status="complete", trigger_ok=True, input_exists=True):
    job_manager = MagicMock()
    ref = MagicMock()
    snapshot = MagicMock(exists=True)
    snapshot.to_dict.return_value = {"status": claim_status}
    ref.get.return_value = snapshot
    job_manager.firestore.db.collection.return_value.document.return_value = ref
    storage = MagicMock()
    storage.file_exists.return_value = input_exists
    storage.list_files.return_value = ["jobs/job123/finals/title_mov.mov", "jobs/job123/finals/lossy_720p_mp4.mp4"]
    worker_service = MagicMock()
    worker_service.trigger_screens_worker = AsyncMock(return_value=trigger_ok)
    return RegenerateService(job_manager=job_manager, storage=storage), storage, worker_service


@pytest.fixture
def no_tx():
    with patch("backend.services.theme_rerender_service.firestore.transactional", lambda fn: fn), \
         patch("google.cloud.firestore.transactional", lambda fn: fn), \
         patch.object(rs, "log_to_job"):
        yield


async def _start(service, worker_service, job, **kwargs):
    with patch("backend.services.worker_service.get_worker_service", return_value=worker_service):
        return await service.start(job, requested_by="customer@example.com", **kwargs)


def _claim(service):
    tx = service.job_manager.firestore.db.transaction.return_value
    updates = [c.args[1] for c in tx.update.call_args_list]
    assert len(updates) == 1
    return updates[0]


class TestRegenerateService:
    @pytest.mark.asyncio
    async def test_claims_and_triggers_screens(self, no_tx):
        service, storage, worker = _service()
        result = await _start(service, worker, _job())
        assert result == {"needs_stems": True, "brand_code": "NOMAD-1234"}
        update = _claim(service)
        assert update["status"] == JobStatus.LYRICS_COMPLETE.value
        assert update["state_data.regen_restore_status"] == "review_complete"
        marker = update["state_data.regenerate"]
        assert marker["brand_code"] == "NOMAD-1234"
        assert marker["notify_customer"] is True
        assert marker["review_token"] == "tok-1"
        assert marker["needs_stems"] is True
        assert marker["source"] == "customer"
        # nothing published is touched: links stay, no YouTube/Dropbox keys cleared
        assert not any(k.startswith("state_data.youtube") or k.startswith("state_data.dropbox") for k in update)
        assert update["file_urls.videos.with_vocals"] is DELETE_FIELD
        assert update["state_data.stems_restore"] is DELETE_FIELD
        assert len(update["state_data.regenerate_requests"]) == 1  # customer run counts
        worker.trigger_screens_worker.assert_awaited_once_with("job123")
        # stale title/end MOVs removed so the encoder can't reuse them; 720p kept
        deleted = [c.args[0] for c in storage.delete_file.call_args_list]
        assert "jobs/job123/finals/title_mov.mov" in deleted
        assert "jobs/job123/finals/lossy_720p_mp4.mp4" not in deleted

    @pytest.mark.asyncio
    async def test_stems_not_needed_when_kept(self, no_tx):
        service, storage, worker = _service()
        result = await _start(service, worker, _job(stems_purged_at=None))
        assert result["needs_stems"] is False

    @pytest.mark.asyncio
    async def test_quiet_admin_regenerate(self, no_tx):
        service, storage, worker = _service()
        await _start(service, worker, _job(), source="admin", notify_customer=False)
        update = _claim(service)
        assert update["state_data.regenerate"]["notify_customer"] is False
        # system/admin runs don't eat the customer's daily allowance
        assert update["state_data.regenerate_requests"] == []

    @pytest.mark.asyncio
    async def test_change_to_private_chain_sets_visibility_guard(self, no_tx):
        service, storage, worker = _service()
        await _start(service, worker, _job(), after="change_to_private")
        update = _claim(service)
        assert update["state_data.regenerate"]["after"] == "change_to_private"
        assert update["state_data.visibility_change_in_progress"] is True

    @pytest.mark.asyncio
    async def test_unknown_after_rejected(self, no_tx):
        service, storage, worker = _service()
        with pytest.raises(RerenderError):
            await _start(service, worker, _job(), after="nope")

    @pytest.mark.asyncio
    async def test_missing_input_rejected_before_claim(self, no_tx):
        service, storage, worker = _service(input_exists=False)
        with pytest.raises(RerenderError, match="original audio"):
            await _start(service, worker, _job())
        service.job_manager.firestore.db.transaction.return_value.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_conflict_when_no_longer_complete(self, no_tx):
        service, storage, worker = _service(claim_status="lyrics_complete")
        with pytest.raises(RerenderConflictError):
            await _start(service, worker, _job())
        worker.trigger_screens_worker.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_trigger_failure_fails_job_keeping_marker(self, no_tx):
        service, storage, worker = _service(trigger_ok=False)
        with pytest.raises(RerenderError) as exc:
            await _start(service, worker, _job())
        assert exc.value.status_code == 503
        failed = service.job_manager.update_job.call_args[0][1]
        assert failed["status"] == JobStatus.FAILED.value
        assert "state_data.regenerate" not in failed


class TestRateLimit:
    def _db(self, existing=None):
        db = MagicMock()
        snap = MagicMock(exists=existing is not None)
        snap.to_dict.return_value = {"requests": existing or []}
        db.collection.return_value.document.return_value.get.return_value = snap
        return db

    def _settings(self):
        return SimpleNamespace(regenerate_max_per_job_per_day=3, regenerate_max_per_user_per_day=2)

    def test_allows_and_records(self, no_tx):
        db = self._db()
        assert check_and_record_rate_limit(db, _job(), "a@b.com", self._settings()) is None
        db.transaction.return_value.set.assert_called_once()

    def test_per_job_limit(self, no_tx):
        now = datetime.now(timezone.utc)
        job = _job(state_data={"regenerate_requests": [now.isoformat()] * 3, "instrumental_selection": "clean"})
        assert "recently" in check_and_record_rate_limit(self._db(), job, "a@b.com", self._settings())

    def test_per_user_limit_and_window(self, no_tx):
        now = datetime.now(timezone.utc)
        recent = [now.isoformat(), now.isoformat()]
        assert "several tracks" in check_and_record_rate_limit(self._db(recent), _job(), "a@b.com", self._settings())
        old = [(now - timedelta(days=2)).isoformat()] * 5
        assert check_and_record_rate_limit(self._db(old), _job(), "a@b.com", self._settings()) is None


# --- stems restore gate -------------------------------------------------------------

class TestStemsRestoreGate:
    @pytest.mark.asyncio
    async def test_not_needed(self):
        from backend.services.stems_restore import NOT_NEEDED, maybe_start_stems_restore
        assert await maybe_start_stems_restore(_job(stems_purged_at=None), MagicMock()) == NOT_NEEDED

    @pytest.mark.asyncio
    async def test_starts_audio_worker(self):
        from backend.services.stems_restore import STARTED, maybe_start_stems_restore
        jm, ws = MagicMock(), MagicMock()
        ws.trigger_audio_worker = AsyncMock(return_value=True)
        with patch("backend.services.worker_service.get_worker_service", return_value=ws):
            assert await maybe_start_stems_restore(_job(), jm) == STARTED
        payload = jm.update_job.call_args[0][1]
        marker = payload["state_data.stems_restore"]
        assert marker["status"] == "running" and marker["attempts"] == 1
        # the screens idempotency mark is dropped so the restore's re-trigger runs
        from backend.services import stems_restore
        assert payload["state_data.screens_progress"] is stems_restore.DELETE_FIELD
        ws.trigger_audio_worker.assert_awaited_once_with("job123")

    @pytest.mark.asyncio
    async def test_duplicate_dispatch_while_running(self):
        from backend.services.stems_restore import IN_PROGRESS, maybe_start_stems_restore
        running = {"status": "running", "attempts": 1, "started_at": datetime.now(timezone.utc).isoformat()}
        job = _job(state_data={"instrumental_selection": "clean", "stems_restore": running})
        assert await maybe_start_stems_restore(job, MagicMock()) == IN_PROGRESS

    @pytest.mark.asyncio
    async def test_stale_run_is_restarted(self):
        from backend.services.stems_restore import STARTED, maybe_start_stems_restore
        stale = {"status": "running", "attempts": 1,
                 "started_at": (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()}
        jm, ws = MagicMock(), MagicMock()
        ws.trigger_audio_worker = AsyncMock(return_value=True)
        with patch("backend.services.worker_service.get_worker_service", return_value=ws):
            job = _job(state_data={"instrumental_selection": "clean", "stems_restore": stale})
            assert await maybe_start_stems_restore(job, jm) == STARTED
        assert jm.update_job.call_args[0][1]["state_data.stems_restore"]["attempts"] == 2

    @pytest.mark.asyncio
    async def test_gives_up_after_max_attempts(self):
        from backend.services.stems_restore import FAILED, maybe_start_stems_restore
        job = _job(state_data={"instrumental_selection": "clean",
                               "stems_restore": {"status": "failed", "attempts": 3}})
        jm = MagicMock()
        assert await maybe_start_stems_restore(job, jm) == FAILED
        jm.mark_job_failed.assert_called_once()

    @pytest.mark.asyncio
    async def test_audio_trigger_failure_fails_job(self):
        from backend.services.stems_restore import FAILED, maybe_start_stems_restore
        jm, ws = MagicMock(), MagicMock()
        ws.trigger_audio_worker = AsyncMock(return_value=False)
        with patch("backend.services.worker_service.get_worker_service", return_value=ws):
            assert await maybe_start_stems_restore(_job(), jm) == FAILED
        jm.mark_job_failed.assert_called_once()

    @pytest.mark.asyncio
    async def test_complete_heals_job_failed_by_earlier_attempt(self):
        from backend.services.stems_restore import complete_stems_restore
        jm, ws = MagicMock(), MagicMock()
        jm.get_job.return_value = _job(status="failed", error_details={"stage": "audio_separation"})
        ws.trigger_screens_worker = AsyncMock(return_value=True)
        with patch("backend.services.worker_service.get_worker_service", return_value=ws):
            await complete_stems_restore("job123", jm)
        update = jm.update_job.call_args[0][1]
        assert update["status"] == "lyrics_complete" and update["error_message"] is None

    def test_restore_stalled(self):
        from backend.services.stems_restore import restore_stalled
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        fresh = datetime.now(timezone.utc).isoformat()
        assert restore_stalled(_job(state_data={"stems_restore": {"status": "running", "started_at": old}}))
        assert not restore_stalled(_job(state_data={"stems_restore": {"status": "running", "started_at": fresh}}))
        assert not restore_stalled(_job(state_data={"stems_restore": {"status": "failed", "started_at": old}}))

    @pytest.mark.asyncio
    async def test_complete_clears_marker_and_resumes_screens(self):
        from backend.services.stems_restore import complete_stems_restore
        jm, ws = MagicMock(), MagicMock()
        jm.get_job.return_value = _job(status="lyrics_complete")
        ws.trigger_screens_worker = AsyncMock(return_value=True)
        with patch("backend.services.worker_service.get_worker_service", return_value=ws):
            assert await complete_stems_restore("job123", jm) is True
        from backend.services import stems_restore
        update = jm.update_job.call_args[0][1]
        assert update["stems_purged_at"] is None
        assert "status" not in update
        assert "state_data.screens_progress" in update
        # compare with the module's own sentinel (other suites stub firestore_v1)
        assert update["state_data.stems_restore"] is stems_restore.DELETE_FIELD
        ws.trigger_screens_worker.assert_awaited_once_with("job123")

    def test_is_restore_run(self):
        from backend.services.stems_restore import is_restore_run
        running = _job(state_data={"stems_restore": {"status": "running"}})
        assert is_restore_run(running)
        # a Cloud Run task retry after a failed attempt still restores
        assert is_restore_run(_job(state_data={"stems_restore": {"status": "failed"}}))
        assert not is_restore_run(_job())
        assert not is_restore_run(_job(stems_purged_at=None, state_data={"stems_restore": {"status": "running"}}))

    @pytest.mark.asyncio
    async def test_screens_worker_defers_to_restore(self):
        from backend.workers import screens_worker
        job = _job(status=JobStatus.LYRICS_COMPLETE, state_data={"instrumental_selection": "clean",
                                                                 "audio_complete": True, "lyrics_complete": True})
        jm = MagicMock()
        jm.get_job.return_value = job
        with patch.object(screens_worker, "JobManager", return_value=jm), \
             patch.object(screens_worker, "StorageService"), \
             patch.object(screens_worker, "create_job_logger", return_value=MagicMock()), \
             patch.object(screens_worker, "_validate_prerequisites", return_value=True), \
             patch("backend.services.stems_restore.maybe_start_stems_restore",
                   new=AsyncMock(return_value="started")) as gate:
            assert await screens_worker.generate_screens("job123") is True
        gate.assert_awaited_once()
        # did not claim GENERATING_SCREENS
        jm.transition_to_state.assert_not_called()


# --- GCS-only pipeline ----------------------------------------------------------------

class TestOrchestratorGcsOnly:
    @pytest.mark.asyncio
    async def test_gcs_only_skips_distribution(self):
        from backend.workers.video_worker_orchestrator import OrchestratorConfig, VideoWorkerOrchestrator
        config = OrchestratorConfig(
            job_id="job123", artist="A", title="T", title_video_path="", karaoke_video_path="",
            instrumental_audio_path="", gcs_only=True, keep_brand_code="NOMAD-1234",
            dropbox_path="/x", brand_prefix="NOMAD", gdrive_folder_id="g", enable_youtube_upload=True,
        )
        orch = VideoWorkerOrchestrator(config=config, job_manager=MagicMock(), storage=MagicMock())
        orch._run_encoding = AsyncMock()
        orch._run_organization = AsyncMock()
        orch._run_distribution = AsyncMock()
        orch._run_notifications = AsyncMock()
        orch._trigger_gdrive_validation = AsyncMock()
        result = await orch.run()
        assert result.success and result.brand_code == "NOMAD-1234"
        orch._run_encoding.assert_awaited_once()
        for stage in (orch._run_organization, orch._run_distribution, orch._run_notifications,
                      orch._trigger_gdrive_validation):
            stage.assert_not_awaited()

    def test_config_flag_follows_marker(self):
        from backend.workers.video_worker_orchestrator import create_orchestrator_config_from_job
        job = MagicMock()
        job.job_id = "job123"
        job.artist, job.title = "A", "T"
        job.review_token = "tok-1"
        job.keep_brand_code = None
        job.existing_instrumental_gcs_path = None
        job.file_urls = {"screens": {}, "videos": {}}
        job.state_data = {"instrumental_selection": "clean", "regenerate": {"review_token": "tok-1",
                                                                            "brand_code": "NOMAD-9"}}
        with patch("backend.services.job_defaults_service.get_effective_distribution_for_job"):
            config = create_orchestrator_config_from_job(job, "/tmp/x")
        assert config.gcs_only is True and config.keep_brand_code == "NOMAD-9"


async def _run_video_worker(state_data, result=None):
    from backend.workers import video_worker
    from backend.tests.test_admin_rerender import _orchestrator_result

    job = MagicMock()
    job.job_id = "job123"
    job.artist, job.title = "A", "T"
    job.tenant_id = None
    job.edit_count = 0
    job.review_token = "tok-1"
    job.existing_instrumental_gcs_path = None
    job.state_data = state_data
    job_manager = MagicMock()
    job_manager.get_job.return_value = job
    orchestrator = MagicMock()
    orchestrator.run = AsyncMock(return_value=result or _orchestrator_result(
        youtube_url=None, dropbox_link=None, gdrive_files={}))
    style = MagicMock()
    style.get_cdg_styles.return_value = None
    native = AsyncMock()
    chained = AsyncMock()
    with patch.object(video_worker, "JobManager", return_value=job_manager), \
         patch.object(video_worker, "StorageService", return_value=MagicMock()), \
         patch.object(video_worker, "create_job_logger", return_value=MagicMock()), \
         patch.object(video_worker, "setup_job_logging", return_value=MagicMock()), \
         patch.object(video_worker, "_validate_prerequisites", return_value=True), \
         patch("backend.services.job_health_service.validate_worker_can_run", return_value=None), \
         patch.object(video_worker, "_setup_working_directory", new=AsyncMock()), \
         patch.object(video_worker, "load_style_config", new=AsyncMock(return_value=style)), \
         patch("backend.workers.video_worker_orchestrator.create_orchestrator_config_from_job", return_value=MagicMock()), \
         patch("backend.workers.video_worker_orchestrator.VideoWorkerOrchestrator", return_value=orchestrator), \
         patch.object(video_worker, "_handle_native_distribution", new=native), \
         patch.object(video_worker, "_upload_results", new=AsyncMock()), \
         patch.object(video_worker, "_store_video_processing_metadata"), \
         patch.object(video_worker, "_run_chained_change_to_private", new=chained), \
         patch("backend.services.community_publish.notify_community_publish", new=AsyncMock()):
        assert await video_worker.generate_video_orchestrated("job123") is True
    return job_manager, native, chained


def _complete(jm):
    calls = [c for c in jm.transition_to_state.call_args_list if c.kwargs.get("new_status") == JobStatus.COMPLETE]
    assert len(calls) == 1
    return calls[0]


class TestVideoWorkerRegenerate:
    @pytest.mark.asyncio
    async def test_regenerate_keeps_published_links_and_notifies(self):
        jm, native, chained = await _run_video_worker({
            "instrumental_selection": "clean",
            "regenerate": {"review_token": "tok-1", "notify_customer": True, "source": "customer"},
        })
        native.assert_not_awaited()
        chained.assert_not_awaited()
        payloads = [c.args[1] for c in jm.update_job.call_args_list]
        final = next(p for p in payloads if "renders_purged_at" in p)
        assert final["renders_purged_at"] is None
        assert not any(k in final for k in ("state_data.youtube_url", "state_data.brand_code",
                                            "state_data.dropbox_link", "state_data.gdrive_files"))
        call = _complete(jm)
        assert call.kwargs["notify"] is True
        assert call.kwargs["count_completion"] is False
        assert call.kwargs["extra_updates"]["state_data.regenerate"] is DELETE_FIELD
        assert "renders_regenerated_at" in call.kwargs["extra_updates"]
        assert call.kwargs["timeline_metadata"]["regenerate"] is True

    @pytest.mark.asyncio
    async def test_quiet_regenerate(self):
        jm, _, _ = await _run_video_worker({
            "instrumental_selection": "clean",
            "regenerate": {"review_token": "tok-1", "notify_customer": False},
        })
        assert _complete(jm).kwargs["notify"] is False

    @pytest.mark.asyncio
    async def test_chained_change_to_private(self):
        jm, _, chained = await _run_video_worker({
            "instrumental_selection": "clean",
            "regenerate": {"review_token": "tok-1", "after": "change_to_private", "requested_by": "u@x.com"},
        })
        chained.assert_awaited_once()
        final = next(c.args[1] for c in jm.update_job.call_args_list if "renders_purged_at" in c.args[1])
        assert "state_data.visibility_change_in_progress" not in final

    @pytest.mark.asyncio
    async def test_normal_completion_also_clears_purge_marker(self):
        jm, native, _ = await _run_video_worker({"instrumental_selection": "clean"})
        native.assert_awaited_once()
        final = next(c.args[1] for c in jm.update_job.call_args_list if "renders_purged_at" in c.args[1])
        assert final["renders_purged_at"] is None and "state_data.youtube_url" in final
        assert _complete(jm).kwargs["count_completion"] is True


# --- routes ----------------------------------------------------------------------------

@pytest.fixture
def client():
    from backend.main import app
    yield TestClient(app), app
    app.dependency_overrides.clear()


def _auth(email="customer@example.com", is_admin=False):
    from backend.services.auth_service import AuthResult, UserType
    return AuthResult(is_valid=True, user_type=UserType.ADMIN if is_admin else UserType.UNLIMITED,
                      remaining_uses=-1, message="Valid", user_email=email, is_admin=is_admin)


def _as(app, auth):
    from backend.api.dependencies import require_admin, require_auth

    async def override():
        return auth

    app.dependency_overrides[require_auth] = override
    app.dependency_overrides.pop(require_admin, None)


class TestRegenerateRoute:
    def _post(self, client, auth, job, rate=None, start=None, headers=None):
        test_client, app = client
        _as(app, auth)
        jm = MagicMock()
        jm.get_job.return_value = job
        start = start or AsyncMock(return_value={"needs_stems": True, "brand_code": "NOMAD-1"})
        with patch("backend.api.routes.jobs.JobManager", return_value=jm), \
             patch.object(rs, "check_and_record_rate_limit", return_value=rate) as limiter, \
             patch.object(RegenerateService, "start", start):
            resp = test_client.post("/api/jobs/job123/regenerate", headers=headers or {})
        return resp, start, limiter

    def test_owner_can_regenerate(self, client):
        resp, start, limiter = self._post(client, _auth(), _job())
        assert resp.status_code == 200, resp.text
        assert resp.json()["needs_stems"] is True
        assert start.await_args.kwargs["source"] == "customer"
        assert start.await_args.kwargs["notify_customer"] is True
        limiter.assert_called_once()

    def test_other_user_forbidden(self, client):
        resp, start, _ = self._post(client, _auth(email="other@example.com"), _job())
        assert resp.status_code == 403
        start.assert_not_awaited()

    def test_rate_limited(self, client):
        resp, start, _ = self._post(client, _auth(), _job(), rate="Please try again tomorrow.")
        assert resp.status_code == 429
        start.assert_not_awaited()

    def test_admin_skips_rate_limit_and_kjbox_source(self, client):
        resp, start, limiter = self._post(client, _auth(email="admin@x.com", is_admin=True), _job(),
                                          headers={"X-Client-Id": "kjbox"})
        assert resp.status_code == 200
        limiter.assert_not_called()
        assert start.await_args.kwargs["source"] == "kjbox"

    def test_customer_cannot_regenerate_a_complete_set(self, client):
        full = _job(renders_purged_at=None, file_urls={
            "lyrics": {"corrections": "c.json"},
            "finals": {"lossy_4k_mp4": "a", "lossy_720p_mp4": "b"}})
        resp, start, _ = self._post(client, _auth(), full)
        assert resp.status_code == 400
        start.assert_not_awaited()

    def test_admin_can_regenerate_a_complete_set(self, client):
        full = _job(renders_purged_at=None, file_urls={
            "lyrics": {"corrections": "c.json"},
            "finals": {"lossy_4k_mp4": "a", "lossy_720p_mp4": "b"}})
        resp, start, _ = self._post(client, _auth(email="admin@x.com", is_admin=True), full)
        assert resp.status_code == 200

    def test_invalid_job_400(self, client):
        resp, start, _ = self._post(client, _auth(), _job(status="in_review"))
        assert resp.status_code == 400

    def test_visibility_change_in_progress_is_409(self, client):
        job = _job(state_data={"instrumental_selection": "clean", "visibility_change_in_progress": True})
        resp, start, _ = self._post(client, _auth(), job)
        assert resp.status_code == 409
        start.assert_not_awaited()

    def test_conflict_409(self, client):
        resp, _, _ = self._post(client, _auth(), _job(),
                                start=AsyncMock(side_effect=RerenderConflictError("busy")))
        assert resp.status_code == 409

    def test_admin_endpoint(self, client):
        test_client, app = client
        _as(app, _auth(email="admin@x.com", is_admin=True))
        jm = MagicMock()
        jm.get_job.return_value = _job()
        start = AsyncMock(return_value={"needs_stems": False, "brand_code": "NOMAD-1"})
        with patch("backend.api.routes.admin.JobManager", return_value=jm), \
             patch.object(RegenerateService, "start", start):
            resp = test_client.post("/api/admin/jobs/job123/regenerate", json={"notify_customer": True})
        assert resp.status_code == 200
        assert start.await_args.kwargs == {"requested_by": "admin@x.com", "source": "admin", "notify_customer": True}

    def test_admin_endpoint_requires_admin(self, client):
        test_client, app = client
        _as(app, _auth())
        resp = test_client.post("/api/admin/jobs/job123/regenerate")
        assert resp.status_code in (401, 403)


class TestArchivedDownload:
    def _get(self, client, job, key="lossy_4k_mp4"):
        test_client, app = client
        _as(app, _auth())
        with patch("backend.api.routes.jobs.job_manager") as jm:
            jm.get_job.return_value = job
            return test_client.get(f"/api/jobs/job123/download/finals/{key}")

    def test_purged_final_is_410_with_regenerate_hint(self, client):
        job = _job(storage_purge={"files": [{"path": "jobs/job123/finals/lossy_4k_mp4.mp4", "bytes": 1}]})
        resp = self._get(client, job)
        assert resp.status_code == 410
        detail = resp.json()["detail"]
        assert detail["code"] == "output_archived"
        assert detail["regenerate_url"] == "/api/jobs/job123/regenerate"

    def test_unknown_key_still_404(self, client):
        job = _job(storage_purge={"files": [{"path": "jobs/job123/finals/lossy_4k_mp4.mp4", "bytes": 1}]})
        assert self._get(client, job, key="nope").status_code == 404

    def test_not_purged_job_404(self, client):
        job = _job(renders_purged_at=None, stems_purged_at=None, storage_purge=None)
        assert self._get(client, job).status_code == 404


class TestInternalRetentionEndpoint:
    def _post(self, client, url, dry_run_setting=True):
        test_client, app = client
        from backend.api.dependencies import require_admin

        async def admin():
            return _auth(email="admin@x.com", is_admin=True)

        app.dependency_overrides[require_admin] = admin
        settings = MagicMock(storage_retention_enabled=True, storage_retention_dry_run=dry_run_setting,
                             gcs_bucket_name="bucket")
        svc = MagicMock()
        svc.run.return_value = {"summary": {}, "jobs": []}
        with patch("backend.config.get_settings", return_value=settings), \
             patch("backend.services.storage_retention.StorageRetentionService", return_value=svc) as cls:
            cls.default_report_path.return_value = "storage-retention/reports/x-dry-run.json"
            resp = test_client.post(url)
        return resp, svc

    def test_global_real_run_refused_while_setting_is_dry(self, client):
        resp, svc = self._post(client, "/api/internal/storage-retention/run?dry_run=false")
        assert resp.status_code == 400
        svc.run.assert_not_called()

    def test_min_age_override_needs_scope(self, client):
        resp, _ = self._post(client, "/api/internal/storage-retention/run?min_age_days=0")
        assert resp.status_code == 400

    def test_scheduled_run_starts_the_cloud_run_job(self, client):
        # The full pass runs as the storage-retention-job Cloud Run Job (CPU is
        # throttled outside requests and Cloudflare cuts requests at 100s).
        ws = MagicMock()
        ws.trigger_storage_retention_job = AsyncMock(return_value=True)
        with patch("backend.services.worker_service.get_worker_service", return_value=ws):
            resp, svc = self._post(client, "/api/internal/storage-retention/run?include_orphans=true")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "started" and body["dry_run"] is True
        assert body["report_path"].startswith("gs://bucket/storage-retention/reports/")
        svc.run.assert_not_called()
        kwargs = ws.trigger_storage_retention_job.await_args.kwargs
        assert kwargs["dry_run"] is True and kwargs["include_orphans"] is True
        assert kwargs["report_path"] == body["report_path"].removeprefix("gs://bucket/")

    def test_job_trigger_failure_is_503(self, client):
        ws = MagicMock()
        ws.trigger_storage_retention_job = AsyncMock(return_value=False)
        with patch("backend.services.worker_service.get_worker_service", return_value=ws):
            resp, _ = self._post(client, "/api/internal/storage-retention/run")
        assert resp.status_code == 503

    def test_scoped_real_run_is_synchronous(self, client):
        resp, svc = self._post(client, "/api/internal/storage-retention/run?job_ids=job123&dry_run=false&min_age_days=0")
        assert resp.status_code == 200 and resp.json()["status"] == "complete"
        kwargs = svc.run.call_args.kwargs
        assert kwargs["dry_run"] is False and kwargs["job_ids"] == ["job123"] and kwargs["min_age_days"] == 0


# --- visibility / edit / retry -------------------------------------------------------

class TestVisibilityChain:
    @pytest.mark.asyncio
    async def test_change_to_private_regenerates_missing_finals_first(self):
        from backend.services.visibility_change_service import VisibilityChangeService
        start = AsyncMock(return_value={"needs_stems": True, "brand_code": "NOMAD-1"})
        with patch.object(RegenerateService, "start", start), \
             patch("backend.workers.video_worker.redistribute_video", new=AsyncMock()) as redistribute:
            result = await VisibilityChangeService(job_manager=MagicMock()).change_to_private(
                "job123", _job(), "customer@example.com")
        assert result["status"] == "processing" and result["reprocessing_required"] is True
        assert start.await_args.kwargs["after"] == "change_to_private"
        assert start.await_args.kwargs["notify_customer"] is False
        redistribute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_never_purged_job_missing_finals_redistributes_as_before(self):
        from backend.services.visibility_change_service import VisibilityChangeService
        start = AsyncMock()
        job = _job(renders_purged_at=None)  # no 4K in file_urls, but nothing was purged
        with patch.object(RegenerateService, "start", start), \
             patch("backend.workers.video_worker.redistribute_video", new=AsyncMock(return_value=False)):
            with pytest.raises(RuntimeError):
                await VisibilityChangeService(job_manager=MagicMock()).change_to_private("job123", job, "u@x.com")
        start.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_chained_call_does_not_loop(self):
        from backend.services.visibility_change_service import VisibilityChangeService
        start = AsyncMock()
        with patch.object(RegenerateService, "start", start), \
             patch("backend.workers.video_worker.redistribute_video", new=AsyncMock(return_value=False)):
            with pytest.raises(RuntimeError):
                await VisibilityChangeService(job_manager=MagicMock()).change_to_private(
                    "job123", _job(), "u@x.com", regenerate_if_missing=False)
        start.assert_not_awaited()


class TestAudioWorkerRestoreMode:
    @pytest.mark.asyncio
    async def test_restore_run_hands_back_to_screens(self):
        from backend.workers import audio_worker
        job = _job(state_data={"instrumental_selection": "clean", "stems_restore": {"status": "running"}},
                   clean_instrumental_model=None, backing_vocals_models=None, other_stems_models=None,
                   url=None, filename="song.flac")
        jm = MagicMock()
        jm.get_job.return_value = job
        processor = MagicMock()
        processor.process_audio_separation.return_value = {"clean_instrumental": {}}
        complete = AsyncMock(return_value=True)
        quick = MagicMock()
        with patch.object(audio_worker, "JobManager", return_value=jm), \
             patch.object(audio_worker, "StorageService"), \
             patch.object(audio_worker, "create_job_logger", return_value=MagicMock()), \
             patch.object(audio_worker, "setup_job_logging", return_value=MagicMock()), \
             patch.object(audio_worker, "worker_registry", MagicMock(register=AsyncMock(), unregister=AsyncMock())), \
             patch.object(audio_worker, "download_audio", new=AsyncMock(return_value="/tmp/song.flac")), \
             patch.object(audio_worker, "_store_audio_source_metadata"), \
             patch.object(audio_worker, "create_audio_processor", return_value=processor), \
             patch.object(audio_worker, "upload_separation_results", new=AsyncMock()), \
             patch.object(audio_worker, "_transcode_review_stems", new=AsyncMock()), \
             patch.object(audio_worker, "start_quick_version", quick), \
             patch("backend.workers.screens_worker._analyze_backing_vocals", new=AsyncMock()), \
             patch("backend.services.stems_restore.complete_stems_restore", new=complete), \
             patch.dict("os.environ", {"MODEL_DIR": "/models"}):
            assert await audio_worker.process_audio_separation("job123") is True
        complete.assert_awaited_once_with("job123", jm)
        quick.assert_not_called()
        jm.mark_audio_complete.assert_not_called()
        jm.advance_to_screens_if_ready.assert_not_called()
        meta_keys = [c.args[1] for c in jm.update_processing_metadata.call_args_list]
        assert "separation_restore" in meta_keys and "separation" not in meta_keys


class TestRetry:
    def _retry(self, client, job, start=None):
        test_client, app = client
        _as(app, _auth())
        job.error_details = {"stage": "stems_restore"}
        job.error_message = "x"
        jm = MagicMock()
        jm.get_job.return_value = job
        jm.transition_to_state.return_value = True
        start = start or AsyncMock(return_value={"needs_stems": True, "brand_code": "NOMAD-1"})
        worker = MagicMock()
        worker.trigger_screens_worker = AsyncMock(return_value=True)
        with patch("backend.api.routes.jobs.job_manager", jm), \
             patch("backend.api.routes.jobs.worker_service", worker), \
             patch.object(RegenerateService, "start", start):
            resp = test_client.post("/api/jobs/job123/retry")
        return resp, start, jm, worker

    def test_failed_regenerate_is_rerun_by_owner(self, client):
        job = _job(status="failed", state_data={
            "instrumental_selection": "clean",
            "regenerate": {"review_token": "tok-1", "source": "customer", "notify_customer": True, "after": None},
        })
        resp, start, _, _ = self._retry(client, job)
        assert resp.status_code == 200, resp.text
        assert resp.json()["retry_stage"] == "regenerate"
        assert start.await_args.kwargs["source"] == "customer"

    def test_stems_purged_edit_failure_goes_back_through_screens(self, client):
        job = _job(status="failed", state_data={"instrumental_selection": "clean"},
                   file_urls={"lyrics": {"corrections": "c.json"},
                              "screens": {"title_png": "t.png"}})
        resp, start, jm, worker = self._retry(client, job)
        assert resp.status_code == 200, resp.text
        assert resp.json()["retry_stage"] == "stems_restore"
        start.assert_not_awaited()
        assert jm.transition_to_state.call_args.kwargs["new_status"] == JobStatus.LYRICS_COMPLETE
        worker.trigger_screens_worker.assert_called_once_with("job123")



class TestRefundGuard:
    @pytest.mark.parametrize("marker", ["regenerate", "admin_rerender", "theme_rerender"])
    def test_cancelling_a_rerun_of_a_delivered_track_does_not_refund(self, marker):
        from backend.services.job_manager import JobManager
        jm = JobManager.__new__(JobManager)
        job = SimpleNamespace(credit_refunded=False, user_email="customer@example.com",
                              state_data={marker: {"source": "customer"}, "credits_charged": 1})
        with patch("backend.services.auth_service.is_admin_email", return_value=False), \
             patch("backend.services.user_service.get_user_service") as users:
            assert jm._refund_credit_for_job("job123", job, reason="job_cancelled") is False
        users.return_value.add_credits.assert_not_called()


class TestStemsRestoreWatchdog:
    def test_recover_stuck_jobs_fails_stalled_restore(self, client):
        test_client, app = client
        from backend.api.dependencies import require_admin

        async def admin():
            return _auth(email="admin@x.com", is_admin=True)

        app.dependency_overrides[require_admin] = admin
        old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        stalled = _job(status="lyrics_complete",
                       state_data={"stems_restore": {"status": "running", "started_at": old}})
        doc = SimpleNamespace(id="job123", to_dict=lambda: {"job_id": "job123", "state_data": stalled.state_data})
        jm = MagicMock()
        jm.get_job.return_value = stalled

        def where(filter=None):
            q = MagicMock()
            status = getattr(filter, "value", None)
            docs = [doc] if status == "lyrics_complete" else []
            q.stream.return_value = docs
            q.limit.return_value.stream.return_value = docs
            return q

        jm.firestore.db.collection.return_value.where.side_effect = where
        with patch("backend.api.routes.internal.JobManager", return_value=jm), \
             patch("google.cloud.firestore_v1.FieldFilter",
                   side_effect=lambda field, op, value: SimpleNamespace(value=value)), \
             patch("backend.services.worker_service.get_worker_service"):
            resp = test_client.post("/api/internal/recover-stuck-jobs")
        assert resp.status_code == 200, resp.text
        assert resp.json()["stems_restore_failed_jobs"] == ["job123"]
        jm.mark_job_failed.assert_called_once()



class TestStorageRetentionWorker:
    def test_cli_runs_service_with_args(self):
        from backend.workers import storage_retention_worker
        svc = MagicMock()
        svc.run.return_value = {"summary": {}}
        with patch("backend.services.storage_retention.StorageRetentionService", return_value=svc):
            code = storage_retention_worker.main(
                ["--dry-run", "false", "--max-jobs", "5", "--include-orphans", "--report-path", "r.json"])
        assert code == 0
        assert svc.run.call_args.kwargs == {"dry_run": False, "max_jobs": 5, "include_orphans": True,
                                             "report_path": "r.json"}

    def test_cli_defaults_to_dry_run(self):
        from backend.workers import storage_retention_worker
        svc = MagicMock()
        svc.run.return_value = {}
        with patch("backend.services.storage_retention.StorageRetentionService", return_value=svc):
            storage_retention_worker.main([])
        assert svc.run.call_args.kwargs["dry_run"] is True

    def test_cli_job_errors_exit_nonzero(self):
        from backend.workers import storage_retention_worker
        svc = MagicMock()
        svc.run.return_value = {"errors": [{"job_id": "x", "error": "boom"}]}
        with patch("backend.services.storage_retention.StorageRetentionService", return_value=svc):
            assert storage_retention_worker.main([]) == 1

    def test_cli_crash_exit_code(self):
        from backend.workers import storage_retention_worker
        with patch("backend.services.storage_retention.StorageRetentionService", side_effect=RuntimeError("x")):
            assert storage_retention_worker.main([]) == 1

    @pytest.mark.asyncio
    async def test_trigger_builds_job_args(self):
        from backend.services.worker_service import WorkerService
        ws = WorkerService.__new__(WorkerService)
        ws._use_cloud_tasks = True
        ws.settings = SimpleNamespace(google_cloud_project="p", cpu_jobs_region="us-east4")
        ws._run_job_with_retry = AsyncMock(return_value=MagicMock(metadata={}))
        with patch("google.cloud.run_v2.JobsClient"):
            assert await ws.trigger_storage_retention_job(False, "r.json", max_jobs=3, include_orphans=True)
        request = ws._run_job_with_retry.await_args.args[1]
        assert request.name == "projects/p/locations/us-east4/jobs/storage-retention-job"
        args = list(request.overrides.container_overrides[0].args)
        assert args[:3] == ["python", "-m", "backend.workers.storage_retention_worker"]
        assert args[3:] == ["--dry-run", "false", "--report-path", "r.json", "--max-jobs", "3", "--include-orphans"]



class TestLostRerenderScreensDispatch:
    def test_parked_rerender_is_retriggered(self, client):
        test_client, app = client
        from backend.api.dependencies import require_admin

        async def admin():
            return _auth(email="admin@x.com", is_admin=True)

        app.dependency_overrides[require_admin] = admin
        state = {"regen_restore_status": "review_complete", "screens_progress": {"stage": "running"},
                 "regenerate": {"review_token": "tok-1"}}
        parked = _job(status="lyrics_complete", state_data=state,
                      updated_at=datetime.now(timezone.utc) - timedelta(minutes=20))
        fresh = _job(job_id="job456", status="lyrics_complete", state_data=dict(state),
                     updated_at=datetime.now(timezone.utc))
        docs = [SimpleNamespace(id=j.job_id, to_dict=(lambda j=j: {"job_id": j.job_id, "state_data": j.state_data}))
                for j in (parked, fresh)]
        jm = MagicMock()
        jm.get_job.side_effect = lambda jid: {"job123": parked, "job456": fresh}[jid]

        def where(filter=None):
            q = MagicMock()
            selected = docs if getattr(filter, "value", None) == "lyrics_complete" else []
            q.stream.return_value = []
            q.limit.return_value.stream.return_value = selected
            return q

        jm.firestore.db.collection.return_value.where.side_effect = where
        ws = MagicMock()
        ws.trigger_screens_worker = AsyncMock(return_value=True)
        with patch("backend.api.routes.internal.JobManager", return_value=jm), \
             patch("google.cloud.firestore_v1.FieldFilter",
                   side_effect=lambda field, op, value: SimpleNamespace(value=value)), \
             patch("backend.services.worker_service.get_worker_service", return_value=ws):
            resp = test_client.post("/api/internal/recover-stuck-jobs")
        assert resp.status_code == 200, resp.text
        assert resp.json()["regen_screens_retriggered_jobs"] == ["job123"]
        ws.trigger_screens_worker.assert_awaited_once_with("job123")
