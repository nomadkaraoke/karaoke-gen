"""
Tests for the admin "Re-render" of any completed job (POST /api/admin/jobs/{id}/rerender).

Covers validation, the service (atomic claim, existing style kept, published
outputs deleted up front, brand code kept rather than recycled, screens-worker
trigger + failure handling, retry of a failed admin re-render), the route
(admin-only, error mapping, notify_customer flag), the brand-code reuse in the
video pipeline, and the completion-notification gating (default off, opt-in,
normal jobs and the tenant theme re-render unaffected).
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from google.cloud.firestore_v1 import DELETE_FIELD

from backend.services.admin_rerender_service import (
    AdminRerenderService,
    suppress_customer_notifications,
    validate_admin_rerender,
)
from backend.services.theme_rerender_service import (
    RerenderConflictError,
    RerenderError,
    rerender_brand_code,
)

SVC = "backend.services.admin_rerender_service"


def _job(**overrides):
    fields = dict(
        job_id="job123",
        status="complete",
        tenant_id=None,
        user_email="customer@example.com",
        artist="Ishay Ribo",
        title="Lev Shel Zahav",
        theme_id="nomad",
        is_private=False,
        dropbox_path="/Karaoke/Tracks-Organized",
        brand_prefix="NOMAD",
        gdrive_folder_id="gdrive-root",
        enable_youtube_upload=True,
        discord_webhook_url=None,
        youtube_description_template=None,
        outputs_deleted_at=None,
        prep_only=False,
        finalise_only=False,
        style_params_gcs_path="jobs/job123/style/style_params.json",
        style_assets={"karaoke_background": "jobs/job123/style/bg.png"},
        state_data={
            "instrumental_selection": "clean",
            "brand_code": "NOMAD-1234",
            "youtube_url": "https://www.youtube.com/watch?v=abc123",
            "dropbox_link": "https://dropbox.com/s/xyz",
            "gdrive_files": {"mp4": "g1", "mp4_720p": "g2", "cdg": "g3"},
        },
        file_urls={"lyrics": {"corrections": "jobs/job123/lyrics/corrections.json"}},
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _published_state(**extra):
    return {**_job().state_data, **extra}


# --- Validation ---------------------------------------------------------------

class TestValidateAdminRerender:
    @pytest.mark.parametrize("overrides", [
        {},                                                   # public consumer job
        {"is_private": True},                                 # private consumer job
        {"tenant_id": "randy-vild"},                          # tenant job
        {"theme_id": None},                                   # no theme needed
        {"state_data": {"instrumental_selection": "custom"}},  # never published
    ])
    def test_valid(self, overrides):
        assert validate_admin_rerender(_job(**overrides)) is None

    @pytest.mark.parametrize("overrides, fragment", [
        ({"status": "rendering_video"}, "Only completed jobs"),
        ({"status": "awaiting_review"}, "Only completed jobs"),
        ({"status": "failed"}, "Only completed jobs"),
        ({"outputs_deleted_at": "2026-09-29T18:47:13Z"}, "outputs were deleted"),
        ({"prep_only": True}, "can't be re-rendered"),
        ({"finalise_only": True}, "can't be re-rendered"),
        ({"state_data": {}}, "instrumental selection"),
        ({"file_urls": {}}, "reviewed lyrics"),
        ({"state_data": {"instrumental_selection": "clean", "visibility_change_in_progress": True}},
         "visibility change"),
    ])
    def test_rejections(self, overrides, fragment):
        assert fragment in validate_admin_rerender(_job(**overrides))

    def test_failed_admin_rerender_is_rerenderable(self):
        job = _job(status="failed", state_data={"instrumental_selection": "clean",
                                                 "admin_rerender": {"brand_code": "NOMAD-1234"}})
        assert validate_admin_rerender(job) is None


# --- Service ------------------------------------------------------------------

def _service(claim_status="complete", trigger_ok=True):
    """AdminRerenderService wired to a fake Firestore transaction + storage."""
    job_manager = MagicMock()
    job_manager.update_job.return_value = None
    doc_ref = MagicMock()
    snapshot = MagicMock(exists=True)
    snapshot.to_dict.return_value = {"status": claim_status}
    doc_ref.get.return_value = snapshot
    job_manager.firestore.db.collection.return_value.document.return_value = doc_ref
    storage = MagicMock()
    storage.list_files.return_value = []
    worker_service = MagicMock()
    worker_service.trigger_screens_worker = AsyncMock(return_value=trigger_ok)
    return AdminRerenderService(job_manager=job_manager, storage=storage), storage, worker_service


@pytest.fixture
def deps():
    """Patch Firestore transactions, job logging and the cleanup helpers."""
    youtube = MagicMock(return_value={"status": "success", "video_id": "abc123"})
    dropbox = MagicMock(return_value={"status": "success", "path": "/x"})
    gdrive = MagicMock(return_value={"status": "success", "files": {}})
    prepare_theme = MagicMock()
    brand_service = MagicMock()
    plan = MagicMock(return_value={"youtube": (True, None), "dropbox": (True, None), "gdrive": (True, None)})
    queue_service = MagicMock()
    queue_service.cancel_upload.return_value = {"status": "skipped", "reason": "no queue entry"}
    with patch("backend.services.theme_rerender_service.firestore.transactional", lambda fn: fn), \
         patch(f"{SVC}.plan_republish", plan), \
         patch("backend.services.youtube_upload_queue_service.get_youtube_upload_queue_service",
               return_value=queue_service), \
         patch(f"{SVC}.log_to_job"), \
         patch(f"{SVC}.delete_youtube_video", youtube), \
         patch(f"{SVC}.delete_dropbox_folder", dropbox), \
         patch(f"{SVC}.delete_gdrive_files", gdrive), \
         patch("backend.api.routes.file_upload._prepare_theme_for_job", prepare_theme), \
         patch("backend.services.brand_code_service.get_brand_code_service", return_value=brand_service):
        yield SimpleNamespace(youtube=youtube, dropbox=dropbox, gdrive=gdrive,
                              prepare_theme=prepare_theme, brand_service=brand_service,
                              plan=plan, queue_service=queue_service)


async def _start(service, worker_service, job, **kwargs):
    with patch("backend.services.worker_service.get_worker_service", return_value=worker_service):
        return await service.start(job, requested_by="admin@nomadkaraoke.com", **kwargs)


def _claim_update(service):
    transaction = service.job_manager.firestore.db.transaction.return_value
    transaction.update.assert_called_once()
    return transaction.update.call_args.args[1]


class TestAdminRerenderService:
    @pytest.mark.asyncio
    async def test_claims_job_for_rerender_without_review(self, deps):
        service, storage, worker_service = _service()
        await _start(service, worker_service, _job())

        update = _claim_update(service)
        assert update["status"] == "lyrics_complete"
        assert update["state_data.regen_restore_status"] == "review_complete"
        assert update["error_message"] is None
        for key in ("screens_progress", "render_progress", "video_progress", "encoding_progress"):
            assert update[f"state_data.{key}"] is DELETE_FIELD
        assert update["file_urls.screens"] is DELETE_FIELD
        assert update["file_urls.videos.with_vocals"] is DELETE_FIELD
        worker_service.trigger_screens_worker.assert_awaited_once_with("job123")

    @pytest.mark.asyncio
    async def test_keeps_existing_style_snapshot(self, deps):
        service, _, worker_service = _service()
        await _start(service, worker_service, _job())

        update = _claim_update(service)
        for key in ("style_params_gcs_path", "style_assets", "theme_id", "color_overrides"):
            assert key not in update
        deps.prepare_theme.assert_not_called()

    @pytest.mark.asyncio
    async def test_marker_records_admin_brand_code_and_previous_outputs(self, deps):
        service, _, worker_service = _service()
        await _start(service, worker_service, _job())

        marker = _claim_update(service)["state_data.admin_rerender"]
        assert marker["requested_by"] == "admin@nomadkaraoke.com"
        assert marker["notify_customer"] is False
        assert marker["brand_code"] == "NOMAD-1234"
        assert marker["previous_outputs"] == {
            "youtube_url": "https://www.youtube.com/watch?v=abc123",
            "dropbox_link": "https://dropbox.com/s/xyz",
            "brand_code": "NOMAD-1234",
            "gdrive_files": {"mp4": "g1", "mp4_720p": "g2", "cdg": "g3"},
        }

    @pytest.mark.asyncio
    async def test_timeline_records_previous_outputs(self, deps):
        service, _, worker_service = _service()
        await _start(service, worker_service, _job())

        event = _claim_update(service)["timeline"].values[0]
        assert event["metadata"]["action"] == "admin_rerender_initiated"
        assert event["metadata"]["previous_outputs"]["youtube_url"] == "https://www.youtube.com/watch?v=abc123"
        assert "admin@nomadkaraoke.com" in event["message"]

    @pytest.mark.asyncio
    async def test_notify_customer_opt_in_recorded(self, deps):
        service, _, worker_service = _service()
        await _start(service, worker_service, _job(), notify_customer=True)
        assert _claim_update(service)["state_data.admin_rerender"]["notify_customer"] is True

    @pytest.mark.asyncio
    async def test_clears_dead_links_but_keeps_brand_code(self, deps):
        service, _, worker_service = _service()
        await _start(service, worker_service, _job())

        update = _claim_update(service)
        for key in ("youtube_url", "youtube_video_id", "dropbox_link", "gdrive_files"):
            assert update[f"state_data.{key}"] is DELETE_FIELD
        assert "state_data.brand_code" not in update

    @pytest.mark.asyncio
    async def test_deletes_published_outputs_up_front(self, deps):
        service, _, worker_service = _service()
        result = await _start(service, worker_service, _job())

        deps.youtube.assert_called_once_with("job123", "https://www.youtube.com/watch?v=abc123")
        deps.dropbox.assert_called_once_with(
            "job123", "/Karaoke/Tracks-Organized", "NOMAD-1234", "Ishay Ribo", "Lev Shel Zahav"
        )
        deps.gdrive.assert_called_once_with(
            "job123", {"mp4": "g1", "mp4_720p": "g2", "cdg": "g3"}, "NOMAD-1234", cleanup_mirror=False
        )
        assert result["cleanup_results"]["brand_code"] == {"status": "kept", "code": "NOMAD-1234"}
        assert result["brand_code"] == "NOMAD-1234"

    @pytest.mark.asyncio
    async def test_brand_code_not_recycled(self, deps):
        service, _, worker_service = _service()
        await _start(service, worker_service, _job())
        deps.brand_service.recycle_brand_code.assert_not_called()

    @pytest.mark.asyncio
    async def test_private_job_uses_private_dropbox_path(self, deps):
        service, _, worker_service = _service()
        settings = MagicMock(default_private_dropbox_path="/Karaoke/Tracks-NonPublished",
                             default_private_brand_prefix="NOMADNP")
        job = _job(is_private=True, state_data={"instrumental_selection": "clean",
                                                 "brand_code": "NOMADNP-0042",
                                                 "dropbox_link": "https://db"})
        with patch("backend.services.job_defaults_service.get_settings", return_value=settings):
            await _start(service, worker_service, job)
        deps.dropbox.assert_called_once_with(
            "job123", "/Karaoke/Tracks-NonPublished", "NOMADNP-0042", "Ishay Ribo", "Lev Shel Zahav"
        )
        deps.youtube.assert_called_once_with("job123", None)

    @pytest.mark.asyncio
    async def test_cleanup_failure_does_not_block_rerender(self, deps):
        deps.youtube.return_value = {"status": "error", "error": "quotaExceeded"}
        deps.gdrive.return_value = {"status": "partial", "files": {"g1": False}}
        service, _, worker_service = _service()
        result = await _start(service, worker_service, _job())
        assert result["cleanup_results"]["youtube"]["status"] == "error"
        worker_service.trigger_screens_worker.assert_awaited_once_with("job123")

    @pytest.mark.asyncio
    async def test_cleanup_results_recorded_on_marker_and_timeline(self, deps):
        service, _, worker_service = _service()
        await _start(service, worker_service, _job())
        payload = service.job_manager.update_job.call_args_list[0].args[1]
        assert payload["state_data.admin_rerender.cleanup_results"]["youtube"]["status"] == "success"
        assert payload["timeline"].values[0]["metadata"]["action"] == "admin_rerender_cleanup"

    @pytest.mark.asyncio
    async def test_deletes_stale_render_artifacts(self, deps):
        service, storage, worker_service = _service()
        storage.list_files.return_value = [
            "jobs/job123/finals/Ishay Ribo - Lev Shel Zahav (Title).mov",
            "jobs/job123/finals/lossy_4k_mp4.mp4",
        ]
        await _start(service, worker_service, _job())
        deleted = {c.args[0] for c in storage.delete_file.call_args_list}
        assert "jobs/job123/screens/title.mov" in deleted
        assert "jobs/job123/videos/with_vocals.mkv" in deleted
        assert "jobs/job123/finals/Ishay Ribo - Lev Shel Zahav (Title).mov" in deleted
        assert "jobs/job123/finals/lossy_4k_mp4.mp4" not in deleted

    @pytest.mark.asyncio
    async def test_conflict_when_job_no_longer_complete(self, deps):
        """A double-click: the second claim sees the job already re-rendering."""
        service, storage, worker_service = _service(claim_status="lyrics_complete")
        with pytest.raises(RerenderConflictError):
            await _start(service, worker_service, _job())
        deps.youtube.assert_not_called()
        deps.dropbox.assert_not_called()
        deps.gdrive.assert_not_called()
        storage.delete_file.assert_not_called()
        worker_service.trigger_screens_worker.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_invalid_job_rejected_before_any_work(self, deps):
        service, storage, worker_service = _service()
        with pytest.raises(RerenderError) as exc:
            await _start(service, worker_service, _job(status="awaiting_review"))
        assert exc.value.status_code == 400
        service.job_manager.firestore.db.transaction.return_value.update.assert_not_called()
        deps.youtube.assert_not_called()
        storage.delete_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_trigger_failure_fails_job_and_keeps_marker_for_retry(self, deps):
        service, _, worker_service = _service(trigger_ok=False)
        with pytest.raises(RerenderError) as exc:
            await _start(service, worker_service, _job())
        assert exc.value.status_code == 503
        payload = service.job_manager.update_job.call_args_list[-1].args[1]
        assert payload["status"] == "failed"
        assert payload["state_data.regen_restore_status"] is DELETE_FIELD
        assert not any("admin_rerender" in key and value is DELETE_FIELD for key, value in payload.items())


class TestRetryFailedAdminRerender:
    def _failed_job(self):
        return _job(status="failed", state_data={
            "instrumental_selection": "clean",
            "brand_code": "NOMAD-1234",
            "admin_rerender": {
                "notify_customer": True,
                "brand_code": "NOMAD-1234",
                "previous_outputs": {"youtube_url": "https://www.youtube.com/watch?v=abc123"},
            },
        })

    @pytest.mark.asyncio
    async def test_claims_failed_admin_rerender_and_keeps_history(self, deps):
        service, _, worker_service = _service(claim_status="failed")
        await _start(service, worker_service, self._failed_job(), notify_customer=True)

        marker = _claim_update(service)["state_data.admin_rerender"]
        assert marker["previous_outputs"]["youtube_url"] == "https://www.youtube.com/watch?v=abc123"
        assert marker["brand_code"] == "NOMAD-1234"
        deps.youtube.assert_called_once_with("job123", None)  # already deleted on attempt 1
        worker_service.trigger_screens_worker.assert_awaited_once_with("job123")

    @pytest.mark.asyncio
    async def test_brand_code_falls_back_to_marker(self, deps):
        service, _, worker_service = _service(claim_status="failed")
        job = self._failed_job()
        del job.state_data["brand_code"]
        result = await _start(service, worker_service, job)
        assert result["brand_code"] == "NOMAD-1234"

    def test_retry_endpoint_reruns_admin_rerender(self, client):
        test_client, app = client
        from backend.api.dependencies import require_auth

        async def override():
            return _auth(is_admin=True)

        app.dependency_overrides[require_auth] = override
        job = self._failed_job()
        job.error_details = {"stage": "screens"}
        job_manager = MagicMock()
        job_manager.get_job.return_value = job
        start = AsyncMock()
        with patch("backend.api.routes.jobs.job_manager", job_manager), \
             patch("backend.api.routes.jobs.JobManager", return_value=job_manager), \
             patch.object(AdminRerenderService, "start", start):
            resp = test_client.post("/api/jobs/job123/retry")
        assert resp.status_code == 200, resp.text
        assert resp.json()["retry_stage"] == "admin_rerender"
        assert start.await_args.kwargs["notify_customer"] is True


# --- Route --------------------------------------------------------------------

@pytest.fixture
def client():
    from backend.main import app
    yield TestClient(app), app
    app.dependency_overrides.clear()


def _auth(email="admin@nomadkaraoke.com", is_admin=True):
    from backend.services.auth_service import AuthResult, UserType
    return AuthResult(
        is_valid=True,
        user_type=UserType.ADMIN if is_admin else UserType.UNLIMITED,
        remaining_uses=-1,
        message="Valid",
        user_email=email,
        is_admin=is_admin,
    )


def _use_auth(app, auth):
    """Authenticate as ``auth`` and run the REAL require_admin check on top.

    The autouse conftest fixture overrides require_admin to always pass; drop
    that so a non-admin is actually rejected.
    """
    from backend.api.dependencies import require_admin, require_auth

    async def override():
        return auth

    app.dependency_overrides[require_auth] = override
    app.dependency_overrides.pop(require_admin, None)


def _post(client, auth, job, body=None, start=None):
    test_client, app = client
    _use_auth(app, auth)
    job_manager = MagicMock()
    job_manager.get_job.return_value = job
    start = start or AsyncMock(return_value={
        "brand_code": "NOMAD-1234",
        "previous_outputs": {"youtube_url": "https://www.youtube.com/watch?v=abc123"},
        "cleanup_results": {"youtube": {"status": "success"}},
    })
    with patch("backend.api.routes.admin.JobManager", return_value=job_manager), \
         patch.object(AdminRerenderService, "start", start):
        kwargs = {"json": body} if body is not None else {}
        return test_client.post("/api/admin/jobs/job123/rerender", **kwargs), start


class TestAdminRerenderRoute:
    def test_admin_starts_rerender_without_notification_by_default(self, client):
        resp, start = _post(client, _auth(), _job(), body={})
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "processing"
        assert data["brand_code"] == "NOMAD-1234"
        assert data["notify_customer"] is False
        assert data["previous_outputs"]["youtube_url"] == "https://www.youtube.com/watch?v=abc123"
        assert start.await_args.kwargs == {"requested_by": "admin@nomadkaraoke.com", "notify_customer": False}

    def test_body_is_optional(self, client):
        resp, start = _post(client, _auth(), _job())
        assert resp.status_code == 200, resp.text
        assert start.await_args.kwargs["notify_customer"] is False

    def test_notify_customer_opt_in(self, client):
        resp, start = _post(client, _auth(), _job(), body={"notify_customer": True})
        assert resp.status_code == 200, resp.text
        assert resp.json()["notify_customer"] is True
        assert start.await_args.kwargs["notify_customer"] is True

    def test_non_admin_forbidden(self, client):
        resp, start = _post(client, _auth(email="customer@example.com", is_admin=False), _job())
        assert resp.status_code == 403
        start.assert_not_awaited()

    def test_missing_job_404(self, client):
        resp, start = _post(client, _auth(), None)
        assert resp.status_code == 404
        start.assert_not_awaited()

    def test_validation_error_maps_to_400(self, client):
        start = AsyncMock(side_effect=RerenderError("Only completed jobs can be re-rendered"))
        resp, _ = _post(client, _auth(), _job(status="rendering_video"), start=start)
        assert resp.status_code == 400
        assert "Only completed jobs" in resp.json()["detail"]

    def test_non_complete_job_rejected_by_real_validation(self, client):
        """End to end through the real service validation (no claim attempted)."""
        test_client, app = client
        _use_auth(app, _auth())
        job_manager = MagicMock()
        job_manager.get_job.return_value = _job(status="encoding")
        with patch("backend.api.routes.admin.JobManager", return_value=job_manager):
            resp = test_client.post("/api/admin/jobs/job123/rerender", json={})
        assert resp.status_code == 400
        job_manager.firestore.db.transaction.assert_not_called()

    def test_in_progress_conflict_maps_to_409(self, client):
        start = AsyncMock(side_effect=RerenderConflictError("already"))
        resp, _ = _post(client, _auth(), _job(), start=start)
        assert resp.status_code == 409

    def test_trigger_failure_maps_to_503(self, client):
        start = AsyncMock(side_effect=RerenderError("Couldn't start", status_code=503))
        resp, _ = _post(client, _auth(), _job(), start=start)
        assert resp.status_code == 503


# --- Brand code reuse in the video pipeline -----------------------------------

class TestBrandCodeReuse:
    def test_rerender_brand_code_reads_admin_marker(self):
        assert rerender_brand_code(_job(state_data={"admin_rerender": {"brand_code": "NOMAD-1234"}})) == "NOMAD-1234"
        assert rerender_brand_code(_job(state_data={"theme_rerender": {"brand_code": "RVILD-0001"}})) == "RVILD-0001"
        assert rerender_brand_code(_job(state_data={"brand_code": "NOMAD-1234"})) is None
        assert rerender_brand_code(_job(state_data={"admin_rerender": {"brand_code": None}})) is None

    def _config(self, state_data):
        from backend.workers.video_worker_orchestrator import create_orchestrator_config_from_job

        job = MagicMock()
        job.job_id = "job123"
        job.artist = "A"
        job.title = "T"
        job.keep_brand_code = None
        job.state_data = state_data
        job.file_urls = {}
        return create_orchestrator_config_from_job(job, temp_dir="/tmp/x")

    def test_orchestrator_keeps_brand_code_and_gates_notification(self):
        config = self._config({"instrumental_selection": "clean", "brand_code": "NOMAD-1234",
                               "admin_rerender": {"brand_code": "NOMAD-1234", "notify_customer": False}})
        assert config.keep_brand_code == "NOMAD-1234"
        assert config.notify_customer is False

    def test_orchestrator_notifies_when_opted_in(self):
        config = self._config({"instrumental_selection": "clean",
                               "admin_rerender": {"brand_code": "NOMAD-1234", "notify_customer": True}})
        assert config.notify_customer is True

    def test_orchestrator_normal_job_notifies_and_allocates(self):
        config = self._config({"instrumental_selection": "clean"})
        assert config.notify_customer is True
        assert config.keep_brand_code is None

    @pytest.mark.asyncio
    async def test_organization_stage_reuses_code_without_allocating(self):
        from backend.workers.video_worker_orchestrator import OrchestratorConfig, VideoWorkerOrchestrator

        config = OrchestratorConfig(
            job_id="job123", artist="A", title="T",
            title_video_path="t", karaoke_video_path="k", instrumental_audio_path="i",
            output_dir="/tmp/x", dropbox_path="/Karaoke", brand_prefix="NOMAD",
            keep_brand_code="NOMAD-1234",
        )
        orchestrator = VideoWorkerOrchestrator(config, job_manager=MagicMock(), storage=MagicMock())
        brand_service = MagicMock()
        with patch("backend.services.brand_code_service.get_brand_code_service", return_value=brand_service):
            await orchestrator._run_organization()
        assert orchestrator.result.brand_code == "NOMAD-1234"
        brand_service.allocate_brand_code.assert_not_called()

    def test_queued_youtube_upload_carries_notify_flag(self):
        from backend.workers.video_worker_orchestrator import OrchestratorConfig, VideoWorkerOrchestrator

        config = OrchestratorConfig(
            job_id="job123", artist="A", title="T",
            title_video_path="t", karaoke_video_path="k", instrumental_audio_path="i",
            output_dir="/tmp/x", notify_customer=False,
        )
        orchestrator = VideoWorkerOrchestrator(config, job_manager=MagicMock(), storage=MagicMock())
        queue_service = MagicMock()
        with patch("backend.services.youtube_upload_queue_service.get_youtube_upload_queue_service",
                   return_value=queue_service):
            orchestrator._queue_youtube_upload("customer@example.com")
        assert queue_service.queue_upload.call_args.kwargs["notify_user"] is False


# --- Completion notification gating -------------------------------------------

class TestSuppressCustomerNotifications:
    def test_default_admin_rerender_suppresses(self):
        assert suppress_customer_notifications(_job(state_data={"admin_rerender": {"brand_code": "X"}})) is True
        assert suppress_customer_notifications(
            _job(state_data={"admin_rerender": {"notify_customer": False}})) is True

    def test_opt_in_notifies(self):
        assert suppress_customer_notifications(
            _job(state_data={"admin_rerender": {"notify_customer": True}})) is False

    def test_normal_job_and_theme_rerender_notify(self):
        assert suppress_customer_notifications(_job()) is False
        assert suppress_customer_notifications(_job(state_data=None)) is False
        assert suppress_customer_notifications(
            _job(state_data={"theme_rerender": {"theme_id": "randy-vild"}})) is False
        assert suppress_customer_notifications(MagicMock()) is False


class TestTransitionNotifyFlag:
    def _job_manager(self):
        from backend.services.job_manager import JobManager

        jm = JobManager.__new__(JobManager)
        jm.firestore = MagicMock()
        jm.validate_state_transition = MagicMock(return_value=True)
        jm.update_job_status = MagicMock()
        jm.get_job = MagicMock(return_value=None)
        jm._trigger_state_notifications = MagicMock()
        return jm

    def test_notifies_by_default(self):
        from backend.models.job import JobStatus
        jm = self._job_manager()
        assert jm.transition_to_state("job123", JobStatus.COMPLETE, progress=100) is True
        jm._trigger_state_notifications.assert_called_once_with("job123", JobStatus.COMPLETE)

    def test_notify_false_skips_notifications(self):
        from backend.models.job import JobStatus
        jm = self._job_manager()
        assert jm.transition_to_state("job123", JobStatus.COMPLETE, progress=100, notify=False) is True
        jm._trigger_state_notifications.assert_not_called()
        jm.update_job_status.assert_called_once()


def _orchestrator_result(**overrides):
    fields = dict(
        success=True, error_message=None, brand_code="NOMAD-1234",
        youtube_url="https://www.youtube.com/watch?v=new456", youtube_upload_queued=False,
        dropbox_link="https://db/new", gdrive_files={"mp4": "n1"}, distribution_warnings=[],
        final_video=None, final_video_mkv=None, final_video_lossy=None, final_video_720p=None,
        final_with_vocals_mp4=None, final_karaoke_cdg_zip=None, final_karaoke_txt_zip=None,
        title_mov=None, end_mov=None, portrait_video=None,
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


async def _run_video_worker(state_data, review_token=None, result=None, community=None):
    """Run generate_video_orchestrated with everything external mocked."""
    from backend.workers import video_worker

    job = MagicMock()
    job.job_id = "job123"
    job.artist = "Ishay Ribo"
    job.title = "Lev Shel Zahav"
    job.tenant_id = None
    job.edit_count = 0
    job.review_token = review_token
    job.state_data = state_data

    job_manager = MagicMock()
    job_manager.get_job.return_value = job
    orchestrator = MagicMock()
    orchestrator.run = AsyncMock(return_value=result or _orchestrator_result())
    style_config = MagicMock()
    style_config.get_cdg_styles.return_value = None

    with patch.object(video_worker, "JobManager", return_value=job_manager), \
         patch.object(video_worker, "StorageService", return_value=MagicMock()), \
         patch.object(video_worker, "create_job_logger", return_value=MagicMock()), \
         patch.object(video_worker, "setup_job_logging", return_value=MagicMock()), \
         patch.object(video_worker, "_validate_prerequisites", return_value=True), \
         patch("backend.services.job_health_service.validate_worker_can_run", return_value=None), \
         patch.object(video_worker, "_setup_working_directory", new=AsyncMock()), \
         patch.object(video_worker, "load_style_config", new=AsyncMock(return_value=style_config)), \
         patch("backend.workers.video_worker_orchestrator.create_orchestrator_config_from_job", return_value=MagicMock()), \
         patch("backend.workers.video_worker_orchestrator.VideoWorkerOrchestrator", return_value=orchestrator), \
         patch.object(video_worker, "_handle_native_distribution", new=AsyncMock()), \
         patch.object(video_worker, "_upload_results", new=AsyncMock()), \
         patch.object(video_worker, "_store_video_processing_metadata"), \
         patch("backend.services.community_publish.notify_community_publish", new=community or AsyncMock()):
        ok = await video_worker.generate_video_orchestrated("job123")
    assert ok is True
    return job_manager


def _complete_call(job_manager):
    from backend.models.job import JobStatus
    calls = [c for c in job_manager.transition_to_state.call_args_list
             if c.kwargs.get("new_status") == JobStatus.COMPLETE]
    assert len(calls) == 1
    return calls[0]


class TestVideoWorkerCompletionGating:
    @pytest.mark.asyncio
    async def test_admin_rerender_completes_without_notifying_by_default(self):
        jm = await _run_video_worker({"instrumental_selection": "clean",
                                      "admin_rerender": {"brand_code": "NOMAD-1234"}})
        call = _complete_call(jm)
        assert call.kwargs["notify"] is False
        assert call.kwargs["timeline_metadata"]["customer_notified"] is False

    @pytest.mark.asyncio
    async def test_admin_rerender_opt_in_notifies(self):
        jm = await _run_video_worker({"instrumental_selection": "clean",
                                      "admin_rerender": {"brand_code": "NOMAD-1234", "notify_customer": True}})
        assert _complete_call(jm).kwargs["notify"] is True

    @pytest.mark.asyncio
    async def test_normal_job_notifies(self):
        jm = await _run_video_worker({"instrumental_selection": "clean"})
        assert _complete_call(jm).kwargs["notify"] is True

    @pytest.mark.asyncio
    async def test_tenant_theme_rerender_still_notifies(self):
        jm = await _run_video_worker({"instrumental_selection": "clean",
                                      "theme_rerender": {"theme_id": "randy-vild"}})
        assert _complete_call(jm).kwargs["notify"] is True

    @pytest.mark.asyncio
    async def test_success_clears_admin_marker_and_records_new_outputs(self):
        jm = await _run_video_worker({"instrumental_selection": "clean",
                                      "admin_rerender": {"brand_code": "NOMAD-1234"}})
        payloads = [c.args[1] for c in jm.update_job.call_args_list]
        final = next(p for p in payloads if "state_data.youtube_url" in p)
        assert final["state_data.admin_rerender"] is DELETE_FIELD
        assert final["state_data.brand_code"] == "NOMAD-1234"
        assert final["state_data.youtube_url"] == "https://www.youtube.com/watch?v=new456"


class TestYouTubeQueueFollowUpEmail:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("entry_extra, expect_email", [
        ({}, True),                       # legacy entries (no flag) still email
        ({"notify_user": True}, True),
        ({"notify_user": False}, False),  # admin re-render without notification
    ])
    async def test_follow_up_email_respects_notify_user(self, entry_extra, expect_email):
        from backend.workers import youtube_queue_processor as qp

        entry = {"job_id": "job123", "user_email": "c@example.com", "artist": "A",
                 "title": "T", "brand_code": "NOMAD-1234", **entry_extra}
        queue_service = MagicMock()
        queue_service.get_queued_uploads.side_effect = [[entry], []]
        queue_service.mark_processing.return_value = True
        quota_service = MagicMock()
        quota_service.check_quota_available.return_value = (True, 10000, "ok")
        send = AsyncMock()
        with patch.object(qp, "get_youtube_upload_queue_service", return_value=queue_service), \
             patch.object(qp, "get_youtube_quota_service", return_value=quota_service), \
             patch.object(qp, "_process_single_upload", new=AsyncMock(return_value="https://youtu.be/x")), \
             patch.object(qp, "_update_job_youtube_url"), \
             patch.object(qp, "_send_youtube_upload_notification", new=send), \
             patch.object(qp, "notify_community_publish", new=AsyncMock()):
            await qp.process_youtube_upload_queue()
        assert send.await_count == (1 if expect_email else 0)


# =============================================================================
# Review follow-ups
# =============================================================================

# --- #6: only delete outputs that will be re-published -------------------------

class TestPlanRepublish:
    def _plan(self, job, yt_configured=True):
        from backend.services.admin_rerender_service import plan_republish
        yt = MagicMock(is_configured=yt_configured)
        with patch("backend.services.youtube_service.get_youtube_service", return_value=yt):
            return plan_republish(job)

    def test_public_job_with_everything_configured(self):
        plan = self._plan(_job())
        assert plan == {"youtube": (True, None), "dropbox": (True, None), "gdrive": (True, None)}

    def test_youtube_disabled(self):
        republish, why = self._plan(_job(enable_youtube_upload=False))["youtube"]
        assert republish is False and "disabled" in why

    def test_youtube_credentials_missing(self):
        republish, why = self._plan(_job(), yt_configured=False)["youtube"]
        assert republish is False and "credentials" in why

    def test_youtube_credential_lookup_error_means_no_republish(self):
        from backend.services.admin_rerender_service import plan_republish
        with patch("backend.services.youtube_service.get_youtube_service", side_effect=RuntimeError("secret")):
            assert plan_republish(_job())["youtube"][0] is False

    def test_private_job_never_republishes_youtube_or_gdrive(self):
        settings = MagicMock(default_private_dropbox_path="/NP", default_private_brand_prefix="NOMADNP")
        with patch("backend.services.job_defaults_service.get_settings", return_value=settings):
            plan = self._plan(_job(is_private=True))
        assert plan["youtube"][0] is False
        assert plan["gdrive"][0] is False
        assert plan["dropbox"] == (True, None)

    def test_dropbox_needs_path_and_prefix(self):
        assert self._plan(_job(dropbox_path=None))["dropbox"][0] is False
        assert self._plan(_job(brand_prefix=None))["dropbox"][0] is False

    def test_gdrive_needs_folder(self):
        assert self._plan(_job(gdrive_folder_id=None))["gdrive"][0] is False


class TestKeepOutputsThatWontBeRepublished:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("destination, link_key", [
        ("youtube", "youtube_url"),
        ("dropbox", "dropbox_link"),
        ("gdrive", "gdrive_files"),
    ])
    async def test_mismatched_destination_is_left_in_place(self, deps, destination, link_key):
        plan = {"youtube": (True, None), "dropbox": (True, None), "gdrive": (True, None)}
        plan[destination] = (False, f"{destination} is not configured")
        deps.plan.return_value = plan
        service, _, worker_service = _service()
        result = await _start(service, worker_service, _job())

        helper = {"youtube": deps.youtube, "dropbox": deps.dropbox, "gdrive": deps.gdrive}
        helper[destination].assert_not_called()
        for other, mock in helper.items():
            if other != destination:
                mock.assert_called_once()
        assert result["cleanup_results"][destination] == {"status": "kept", "reason": f"{destination} is not configured"}
        assert any(destination in w for w in result["warnings"])

        update = _claim_update(service)
        assert f"state_data.{link_key}" not in update  # live link kept
        marker = update["state_data.admin_rerender"]
        assert marker["kept_outputs"] == {link_key: _job().state_data[link_key]}
        assert marker["warnings"] == result["warnings"]
        assert update["timeline"].values[0]["metadata"]["warnings"] == result["warnings"]

    @pytest.mark.asyncio
    async def test_nothing_published_means_no_warnings(self, deps):
        deps.plan.return_value = {"youtube": (False, "off"), "dropbox": (False, "off"), "gdrive": (False, "off")}
        service, _, worker_service = _service()
        result = await _start(service, worker_service, _job(state_data={"instrumental_selection": "clean"}))
        assert result["warnings"] == []

    @pytest.mark.asyncio
    async def test_route_returns_warnings(self, client):
        start = AsyncMock(return_value={"brand_code": "NOMAD-1", "previous_outputs": {},
                                        "cleanup_results": {}, "warnings": ["youtube output left in place"]})
        resp, _ = _post(client, _auth(), _job(), start=start)
        assert resp.json()["warnings"] == ["youtube output left in place"]

    @pytest.mark.asyncio
    async def test_video_worker_keeps_link_of_unpublished_output(self):
        jm = await _run_video_worker({
            "instrumental_selection": "clean",
            "admin_rerender": {"brand_code": "NOMAD-1234",
                               "kept_outputs": {"dropbox_link": "https://db/old"}},
        }, result=_orchestrator_result(dropbox_link=None))
        final = next(c.args[1] for c in jm.update_job.call_args_list if "state_data.youtube_url" in c.args[1])
        assert final["state_data.dropbox_link"] == "https://db/old"
        assert final["state_data.youtube_url"] == "https://www.youtube.com/watch?v=new456"


# --- #1: stale deferred YouTube upload ----------------------------------------

class TestStaleYouTubeQueueEntry:
    @pytest.mark.asyncio
    async def test_rerender_cancels_pending_queue_entry(self, deps):
        deps.queue_service.cancel_upload.return_value = {"status": "cancelled", "previous_status": "queued"}
        service, _, worker_service = _service()
        result = await _start(service, worker_service, _job())
        deps.queue_service.cancel_upload.assert_called_once_with("job123", reason="admin_rerender")
        assert result["cleanup_results"]["youtube_queue"]["status"] == "cancelled"
        assert _claim_update(service)["state_data.youtube_upload_queued"] is DELETE_FIELD

    @pytest.mark.asyncio
    async def test_queue_cancel_failure_is_best_effort(self, deps):
        deps.queue_service.cancel_upload.side_effect = RuntimeError("firestore down")
        service, _, worker_service = _service()
        result = await _start(service, worker_service, _job())
        assert result["cleanup_results"]["youtube_queue"]["status"] == "error"
        worker_service.trigger_screens_worker.assert_awaited_once()

    def _queue_service(self, status):
        from backend.services.youtube_upload_queue_service import YouTubeUploadQueueService
        db = MagicMock()
        doc = MagicMock(exists=status is not None)
        doc.to_dict.return_value = {"status": status}
        doc_ref = db.collection.return_value.document.return_value
        doc_ref.get.return_value = doc
        return YouTubeUploadQueueService(db=db), db

    @pytest.mark.parametrize("status, expected", [
        ("queued", "cancelled"), ("failed", "cancelled"), ("processing", "processing"),
        ("completed", "skipped"), (None, "skipped"),
    ])
    def test_cancel_upload(self, status, expected):
        service, db = self._queue_service(status)
        with patch("backend.services.youtube_upload_queue_service.firestore.transactional", lambda fn: fn):
            result = service.cancel_upload("job123", reason="admin_rerender")
        assert result["status"] == expected
        transaction = db.transaction.return_value
        if expected == "cancelled":
            assert transaction.update.call_args.args[1]["status"] == "cancelled"
        else:
            transaction.update.assert_not_called()

    async def _process(self, job, entry_extra=None):
        from backend.workers import youtube_queue_processor as qp

        entry = {"job_id": "job123", "user_email": "c@example.com", "artist": "A",
                 "title": "T", "brand_code": "NOMAD-1234", **(entry_extra or {})}
        queue_service = MagicMock()
        queue_service.get_queued_uploads.side_effect = [[entry], []]
        queue_service.mark_processing.return_value = True
        quota_service = MagicMock()
        quota_service.check_quota_available.return_value = (True, 10000, "ok")
        job_manager = MagicMock()
        job_manager.get_job.return_value = job
        upload = AsyncMock(return_value="https://youtu.be/x")
        send = AsyncMock()
        community = AsyncMock()
        with patch.object(qp, "get_youtube_upload_queue_service", return_value=queue_service), \
             patch.object(qp, "get_youtube_quota_service", return_value=quota_service), \
             patch.object(qp, "JobManager", return_value=job_manager), \
             patch.object(qp, "_process_single_upload", new=upload), \
             patch.object(qp, "_update_job_youtube_url"), \
             patch.object(qp, "_send_youtube_upload_notification", new=send), \
             patch.object(qp, "notify_community_publish", new=community):
            await qp.process_youtube_upload_queue()
        return SimpleNamespace(upload=upload, send=send, community=community, queue=queue_service)

    @pytest.mark.asyncio
    async def test_processor_defers_upload_while_rerender_in_progress(self):
        job = _job(status="rendering_video", state_data={"admin_rerender": {"brand_code": "NOMAD-1234"}})
        r = await self._process(job)
        r.upload.assert_not_awaited()
        r.queue.mark_processing.assert_not_called()
        r.send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_processor_suppresses_emails_while_marker_present(self):
        """Legacy entry (no notify_user) processed during a silent admin re-render."""
        job = _job(status="complete", state_data={"admin_rerender": {"brand_code": "NOMAD-1234"}})
        r = await self._process(job)
        r.upload.assert_awaited_once()
        r.send.assert_not_awaited()
        assert r.community.await_args.kwargs["notify_voters"] is False

    @pytest.mark.asyncio
    async def test_processor_normal_job_emails(self):
        r = await self._process(_job(state_data={}))
        r.send.assert_awaited_once()
        assert r.community.await_args.kwargs["notify_voters"] is True


# --- #2: retry is admin-only for an admin re-render -------------------------------

class TestRetryRequiresAdmin:
    def _retry(self, client, auth, job):
        test_client, app = client
        _use_auth(app, auth)
        job_manager = MagicMock()
        job_manager.get_job.return_value = job
        start = AsyncMock()
        with patch("backend.api.routes.jobs.job_manager", job_manager), \
             patch("backend.api.routes.jobs.JobManager", return_value=job_manager), \
             patch.object(AdminRerenderService, "start", start):
            return test_client.post("/api/jobs/job123/retry"), start, job_manager

    def _failed(self, **marker):
        job = _job(status="failed", user_email="customer@example.com",
                   state_data={"instrumental_selection": "clean",
                               "admin_rerender": {"brand_code": "NOMAD-1234", **marker}})
        job.error_details = {"stage": "screens"}
        return job

    def test_owner_cannot_retry_admin_rerender(self, client):
        resp, start, jm = self._retry(client, _auth(email="customer@example.com", is_admin=False), self._failed())
        assert resp.status_code == 403
        assert "admin" in resp.json()["detail"].lower()
        start.assert_not_awaited()
        jm.transition_to_state.assert_not_called()

    def test_owner_blocked_even_when_screens_exist(self, client):
        job = self._failed()
        job.file_urls = {**job.file_urls, "screens": {"title_png": "x"}, "videos": {"with_vocals": "v"}}
        resp, _, jm = self._retry(client, _auth(email="customer@example.com", is_admin=False), job)
        assert resp.status_code == 403
        jm.transition_to_state.assert_not_called()

    def test_admin_can_retry(self, client):
        resp, start, _ = self._retry(client, _auth(), self._failed())
        assert resp.status_code == 200, resp.text
        assert start.await_args.kwargs["requested_by"] == "admin@nomadkaraoke.com"

    def test_owner_can_retry_after_stale_marker(self, client):
        """A marker from an earlier run (review_token changed) doesn't gate a normal retry."""
        job = self._failed(review_token="old-token")
        job.review_token = "new-token"
        job.file_urls = {**job.file_urls, "screens": {"title_png": "x"}, "videos": {"with_vocals": "v"}}
        resp, start, _ = self._retry(client, _auth(email="customer@example.com", is_admin=False), job)
        assert resp.status_code != 403
        start.assert_not_awaited()


# --- #3: marker only applies to its own run ------------------------------------

class TestMarkerScopedToRun:
    @pytest.mark.asyncio
    async def test_claim_records_review_token(self, deps):
        service, _, worker_service = _service()
        await _start(service, worker_service, _job(review_token="tok-1"))
        assert _claim_update(service)["state_data.admin_rerender"]["review_token"] == "tok-1"

    def _job_with_marker(self, current_token, **marker):
        return _job(review_token=current_token, state_data={
            "instrumental_selection": "clean",
            "admin_rerender": {"brand_code": "NOMAD-1234", "review_token": "tok-1", **marker},
        })

    def test_marker_honoured_in_same_run(self):
        job = self._job_with_marker("tok-1")
        assert suppress_customer_notifications(job) is True
        assert rerender_brand_code(job) == "NOMAD-1234"

    def test_stale_marker_ignored_after_new_review(self):
        job = self._job_with_marker("tok-2")
        assert suppress_customer_notifications(job) is False
        assert rerender_brand_code(job) is None
        assert "Only completed jobs" in validate_admin_rerender(
            _job(status="failed", review_token="tok-2",
                 state_data={"instrumental_selection": "clean",
                             "admin_rerender": {"review_token": "tok-1"}}))

    @pytest.mark.asyncio
    async def test_video_worker_ignores_stale_marker(self):
        job_state = {"instrumental_selection": "clean",
                     "admin_rerender": {"brand_code": "NOMAD-1234", "review_token": "tok-1"}}
        jm = await _run_video_worker(job_state, review_token="tok-2")
        assert _complete_call(jm).kwargs["notify"] is True

    def test_clear_update(self):
        from backend.services.admin_rerender_service import clear_admin_rerender_update
        assert clear_admin_rerender_update(self._job_with_marker("tok-2")) == {
            "state_data.admin_rerender": DELETE_FIELD}
        assert clear_admin_rerender_update(_job()) == {}

    def test_edit_clears_marker(self, client):
        from backend.api.routes import jobs as jobs_routes
        test_client, app = client
        _use_auth(app, _auth())
        job = _job(state_data={"instrumental_selection": "clean", "admin_rerender": {"brand_code": "X"}})
        job.edit_count = 0
        job.tempo_factor = None
        job.review_token = "tok"
        job_manager = MagicMock()
        job_manager.get_job.return_value = job
        fs = MagicMock()
        job_ref = fs.return_value.db.collection.return_value.document.return_value
        with patch.object(jobs_routes, "job_manager", job_manager), \
             patch.object(jobs_routes, "FirestoreService", fs), \
             patch.object(jobs_routes, "StorageService"), \
             patch.object(jobs_routes, "log_to_job"), \
             patch("backend.services.published_outputs_cleanup.delete_youtube_video",
                   return_value={"status": "skipped"}), \
             patch("backend.services.published_outputs_cleanup.delete_dropbox_folder",
                   return_value={"status": "skipped"}), \
             patch("backend.services.published_outputs_cleanup.delete_gdrive_files",
                   return_value={"status": "skipped"}):
            resp = test_client.post("/api/jobs/job123/edit", json={})
        assert resp.status_code == 200, resp.text
        assert job_ref.update.call_args.args[0]["state_data.admin_rerender"] is DELETE_FIELD

    def test_admin_reset_clears_marker(self, client):
        from backend.api.routes import admin as admin_routes
        test_client, app = client
        _use_auth(app, _auth())
        job = _job(status="failed", state_data={"instrumental_selection": "clean",
                                                 "admin_rerender": {"brand_code": "X"}})
        job_manager = MagicMock()
        job_manager.get_job.return_value = job
        user_service = MagicMock()
        job_ref = user_service.db.collection.return_value.document.return_value
        app.dependency_overrides[admin_routes.get_user_service] = lambda: user_service
        with patch.object(admin_routes, "JobManager", return_value=job_manager):
            resp = test_client.post("/api/admin/jobs/job123/reset", json={"target_state": "awaiting_review"})
        assert resp.status_code == 200, resp.text
        payloads = [c.args[0] for c in job_ref.update.call_args_list]
        assert any(p.get("state_data.admin_rerender") is DELETE_FIELD for p in payloads)


# --- #4: legacy completion path ---------------------------------------------------

class TestLegacyVideoWorkerPath:
    @pytest.mark.asyncio
    async def test_legacy_completion_drops_markers_and_gates_notifications(self):
        from backend.workers import video_worker

        job = MagicMock()
        job.job_id = "job123"
        job.artist = "A"
        job.title = "T"
        job.review_token = None
        job.edit_count = 0
        job.state_data = {"instrumental_selection": "clean", "theme_rerender": {"theme_id": "x"},
                          "admin_rerender": {"brand_code": "NOMAD-1234",
                                             "kept_outputs": {"gdrive_files": {"mp4": "old"}}}}
        job_manager = MagicMock()
        job_manager.get_job.return_value = job
        result = {"brand_code": "NOMAD-1234", "youtube_url": None, "dropbox_link": "https://db/new",
                  "gdrive_files": None}
        community = AsyncMock()
        with patch.object(video_worker, "JobManager", return_value=job_manager), \
             patch.object(video_worker, "StorageService", return_value=MagicMock()), \
             patch.object(video_worker, "create_job_logger", return_value=MagicMock()), \
             patch.object(video_worker, "setup_job_logging", return_value=MagicMock()), \
             patch.object(video_worker, "_validate_prerequisites", return_value=True), \
             patch.object(video_worker, "validate_worker_can_run", return_value=None), \
             patch("backend.services.job_health_service.validate_worker_can_run", return_value=None), \
             patch.object(video_worker, "_setup_working_directory", new=AsyncMock()), \
             patch.object(video_worker, "load_style_config", new=AsyncMock(return_value=MagicMock())), \
             patch.object(video_worker, "get_encoding_service", return_value=MagicMock(is_enabled=True)), \
             patch.object(video_worker, "_encode_via_gce", new=AsyncMock(return_value=result)), \
             patch.object(video_worker, "_handle_native_distribution", new=AsyncMock()), \
             patch.object(video_worker, "_upload_results", new=AsyncMock()), \
             patch.object(video_worker, "get_rclone_service", return_value=MagicMock()), \
             patch.object(video_worker, "get_youtube_service", return_value=MagicMock(is_configured=False)), \
             patch("backend.services.community_publish.notify_community_publish", new=community):
            ok = await video_worker.generate_video_legacy("job123")
        assert ok is True, job_manager.mock_calls[-5:]
        state = next(c.args[1]["state_data"] for c in job_manager.update_job.call_args_list
                     if "state_data" in c.args[1])
        assert "admin_rerender" not in state and "theme_rerender" not in state
        assert state["gdrive_files"] == {"mp4": "old"}
        from backend.models.job import JobStatus
        complete = [c for c in job_manager.transition_to_state.call_args_list
                    if c.kwargs.get("new_status") == JobStatus.COMPLETE]
        assert complete[-1].kwargs["notify"] is False


# --- #5: failures after the claim leave the job retryable ---------------------------

class TestFailureAfterClaim:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("breakage", ["artifacts", "youtube", "dropbox", "gdrive", "trigger"])
    async def test_exception_marks_failed_with_marker_kept(self, deps, breakage):
        service, storage, worker_service = _service()
        boom = RuntimeError(f"{breakage} exploded")
        if breakage == "artifacts":
            storage.list_files.side_effect = None
            with patch(f"{SVC}.delete_regenerated_artifacts", side_effect=boom):
                with pytest.raises(RerenderError) as exc:
                    await _start(service, worker_service, _job())
        else:
            if breakage == "trigger":
                worker_service.trigger_screens_worker.side_effect = boom
            else:
                getattr(deps, breakage).side_effect = boom
            with pytest.raises(RerenderError) as exc:
                await _start(service, worker_service, _job())
        assert exc.value.status_code == 500
        assert "retry" in str(exc.value).lower()
        payload = service.job_manager.update_job.call_args_list[-1].args[1]
        assert payload["status"] == "failed"
        assert payload["error_details"]["stage"] == "admin_rerender"
        assert "state_data.admin_rerender" not in payload  # marker kept → retryable

    def test_route_maps_post_claim_failure_to_500(self, client):
        start = AsyncMock(side_effect=RerenderError("failed to start; retry it", status_code=500))
        resp, _ = _post(client, _auth(), _job(), start=start)
        assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_blocking_work_runs_off_the_event_loop(self, deps):
        service, _, worker_service = _service()
        calls = []
        real_to_thread = __import__("asyncio").to_thread

        async def spy(fn, *args, **kwargs):
            calls.append(fn)
            return await real_to_thread(fn, *args, **kwargs)

        with patch(f"{SVC}.asyncio.to_thread", side_effect=spy):
            await _start(service, worker_service, _job())
        # plan_republish (mocked in deps), the Firestore claim and the post-claim
        # deletions all go through to_thread.
        names = {getattr(fn, "__name__", None) for fn in calls}
        assert {"claim_for_rerender", "_after_claim"} <= names
        assert deps.plan in calls


# --- #7: Discord + community voters ------------------------------------------------

class TestDiscordAndCommunityGating:
    def _orchestrator(self, notify):
        from backend.workers.video_worker_orchestrator import OrchestratorConfig, VideoWorkerOrchestrator
        config = OrchestratorConfig(
            job_id="job123", artist="A", title="T",
            title_video_path="t", karaoke_video_path="k", instrumental_audio_path="i",
            discord_webhook_url="https://discord/webhook", notify_customer=notify,
        )
        orchestrator = VideoWorkerOrchestrator(config, job_manager=MagicMock(), storage=MagicMock())
        orchestrator.result.youtube_url = "https://youtu.be/new"
        orchestrator._discord_service = MagicMock()
        return orchestrator

    @pytest.mark.asyncio
    async def test_discord_skipped_without_notification(self):
        o = self._orchestrator(False)
        await o._run_notifications()
        o._discord_service.post_video_notification.assert_not_called()

    @pytest.mark.asyncio
    async def test_discord_posts_normally(self):
        o = self._orchestrator(True)
        await o._run_notifications()
        o._discord_service.post_video_notification.assert_called_once_with("https://youtu.be/new")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("marker, expected", [
        ({"brand_code": "NOMAD-1234"}, False),
        ({"brand_code": "NOMAD-1234", "notify_customer": True}, True),
        (None, True),
    ])
    async def test_video_worker_passes_notify_voters(self, marker, expected):
        state = {"instrumental_selection": "clean"}
        if marker:
            state["admin_rerender"] = marker
        community = AsyncMock()
        await _run_video_worker(state, community=community)
        assert community.await_args.kwargs["notify_voters"] is expected

    def _request(self, voters_notified=False):
        return SimpleNamespace(id="req1", voters_notified=voters_notified, owner_email="o@x.com",
                               submitted_by=None, notified_voters=[], artist="A", title="T")

    @pytest.mark.asyncio
    async def test_community_publish_updates_url_but_skips_voter_emails(self):
        from backend.services.community_publish import notify_community_publish
        service = MagicMock()
        service.get_by_job_id.return_value = self._request()
        service.list_upvoters.return_value = ["v@x.com"]
        notifier = MagicMock()
        notifier.send_community_track_live_email = AsyncMock(return_value=True)
        with patch("backend.services.song_request_service.get_song_request_service", return_value=service), \
             patch("backend.services.job_notification_service.get_job_notification_service", return_value=notifier):
            assert await notify_community_publish("job123", "https://youtu.be/new", notify_voters=False) == "req1"
        service.mark_published.assert_called_once_with("req1", "https://youtu.be/new")
        notifier.send_community_track_live_email.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_community_publish_emails_voters_by_default(self):
        from backend.services.community_publish import notify_community_publish
        service = MagicMock()
        service.get_by_job_id.return_value = self._request()
        service.list_upvoters.return_value = ["v@x.com"]
        notifier = MagicMock()
        notifier.send_community_track_live_email = AsyncMock(return_value=True)
        with patch("backend.services.song_request_service.get_song_request_service", return_value=service), \
             patch("backend.services.job_notification_service.get_job_notification_service", return_value=notifier):
            await notify_community_publish("job123", "https://youtu.be/new")
        notifier.send_community_track_live_email.assert_awaited_once()


# --- #8: Edit's Dropbox folder name now matches the uploader (sanitised) -------------

class TestEditDropboxPathSanitised:
    def test_edit_deletes_sanitised_dropbox_folder(self, client):
        """Edit used the raw "Artist - Title"; the uploader sanitises it, so names with
        special characters were never found. Edit now deletes the folder the
        distribution step actually created."""
        from backend.api.routes import jobs as jobs_routes
        from karaoke_gen.utils import sanitize_filename

        test_client, app = client
        _use_auth(app, _auth())
        artist, title = "AC/DC", 'What’s "Up"? / Live: Pt. 1'
        job = _job(artist=artist, title=title,
                   state_data={"instrumental_selection": "clean", "brand_code": "NOMAD-0042",
                               "dropbox_link": "https://db/x"})
        job.edit_count = 0
        job.tempo_factor = None
        job.review_token = "tok"
        job_manager = MagicMock()
        job_manager.get_job.return_value = job
        dropbox = MagicMock(is_configured=True)
        dropbox.delete_folder.return_value = True
        with patch.object(jobs_routes, "job_manager", job_manager), \
             patch.object(jobs_routes, "FirestoreService"), \
             patch.object(jobs_routes, "StorageService"), \
             patch.object(jobs_routes, "log_to_job"), \
             patch("backend.services.dropbox_service.get_dropbox_service", return_value=dropbox), \
             patch("backend.services.brand_code_service.get_brand_code_service"):
            resp = test_client.post("/api/jobs/job123/edit", json={})
        assert resp.status_code == 200, resp.text
        expected = f"/Karaoke/Tracks-Organized/NOMAD-0042 - {sanitize_filename(artist)} - {sanitize_filename(title)}"
        assert expected != f"/Karaoke/Tracks-Organized/NOMAD-0042 - {artist} - {title}"
        dropbox.delete_folder.assert_called_once_with(expected)
        assert resp.json()["cleanup_results"]["dropbox"] == {"status": "success", "path": expected}
