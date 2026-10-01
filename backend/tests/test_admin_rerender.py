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

from backend.services.admin_rerender_service import plan_republish as _REAL_PLAN_REPUBLISH  # noqa: E402


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

def _service(claim_status="complete", trigger_ok=True, queue_status=None):
    """AdminRerenderService wired to a fake Firestore (job + youtube_upload_queue docs)."""
    job_manager = MagicMock()
    job_manager.update_job.return_value = None
    docs = {}
    for name, status in (("jobs", claim_status), ("youtube_upload_queue", queue_status)):
        ref = MagicMock(name=f"{name}_ref")
        snapshot = MagicMock(exists=status is not None)
        snapshot.to_dict.return_value = {"status": status} if status is not None else None
        ref.get.return_value = snapshot
        docs[name] = ref
    collections = {name: MagicMock(**{"document.return_value": ref}) for name, ref in docs.items()}
    job_manager.firestore.db.collection.side_effect = lambda name: collections[name]
    job_manager.firestore.db._docs = docs
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
    with patch("backend.services.theme_rerender_service.firestore.transactional", lambda fn: fn), \
         patch(f"{SVC}.plan_republish", plan), \
         patch(f"{SVC}.log_to_job"), \
         patch(f"{SVC}.delete_youtube_video", youtube), \
         patch(f"{SVC}.delete_dropbox_folder", dropbox), \
         patch(f"{SVC}.delete_gdrive_files", gdrive), \
         patch("backend.api.routes.file_upload._prepare_theme_for_job", prepare_theme), \
         patch("backend.services.brand_code_service.get_brand_code_service", return_value=brand_service):
        yield SimpleNamespace(youtube=youtube, dropbox=dropbox, gdrive=gdrive,
                              prepare_theme=prepare_theme, brand_service=brand_service,
                              plan=plan)


async def _start(service, worker_service, job, **kwargs):
    with patch("backend.services.worker_service.get_worker_service", return_value=worker_service):
        return await service.start(job, requested_by="admin@nomadkaraoke.com", **kwargs)


def _job_updates(service):
    transaction = service.job_manager.firestore.db.transaction.return_value
    job_ref = service.job_manager.firestore.db._docs["jobs"]
    return [c.args[1] for c in transaction.update.call_args_list if c.args[0] is job_ref]


def _queue_updates(service):
    transaction = service.job_manager.firestore.db.transaction.return_value
    queue_ref = service.job_manager.firestore.db._docs["youtube_upload_queue"]
    return [c.args[1] for c in transaction.update.call_args_list if c.args[0] is queue_ref]


def _claim_update(service):
    updates = _job_updates(service)
    assert len(updates) == 1, updates
    return updates[0]


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
        # The marker is NOT dropped with the distribution results...
        assert "state_data.admin_rerender" not in final
        # ...but atomically with the COMPLETE transition.
        assert _complete_call(jm).kwargs["extra_updates"] == {"state_data.admin_rerender": DELETE_FIELD}
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
        assert {"claim_with_youtube_queue", "_after_claim"} <= names
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
        sanitised = f"/Karaoke/Tracks-Organized/NOMAD-0042 - {sanitize_filename(artist)} - {sanitize_filename(title)}"
        dropbox.file_exists.side_effect = lambda path: path == sanitised  # only the uploader's name exists
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
        assert resp.json()["cleanup_results"]["dropbox"] == {
            "status": "success", "path": expected, "deleted": [expected]}


# =============================================================================
# Second review pass
# =============================================================================

# --- R2#1: a rejected restart keeps the marker -------------------------------------

class TestRestartMarkerAtomic:
    def _restart(self, client, job, body):
        from backend.api.routes import admin as admin_routes
        test_client, app = client
        _use_auth(app, _auth())
        job_manager = MagicMock()
        job_manager.get_job.return_value = job
        job_ref = job_manager.firestore.db.collection.return_value.document.return_value
        worker_service = MagicMock()
        worker_service.trigger_screens_worker = AsyncMock(return_value=True)
        with patch.object(admin_routes, "JobManager", return_value=job_manager), \
             patch("backend.services.worker_service.get_worker_service", return_value=worker_service):
            resp = test_client.post("/api/admin/jobs/job123/restart", json=body)
        return resp, job_manager, job_ref

    def _failed_job(self, **state):
        return _job(status="failed", url=None, input_media_gcs_path="uploads/x.flac",
                    state_data={"instrumental_selection": "clean",
                                "admin_rerender": {"brand_code": "NOMAD-1234"}, **state})

    def test_rejected_restart_keeps_marker(self, client):
        resp, jm, job_ref = self._restart(client, self._failed_job(),
                                          {"preserve_audio_stems": True, "delete_outputs": False})
        assert resp.status_code == 400
        jm.update_job.assert_not_called()
        job_ref.update.assert_not_called()

    def test_preserving_restart_clears_marker_atomically(self, client):
        job = self._failed_job(audio_progress={"stage": "audio_complete"},
                               lyrics_progress={"stage": "lyrics_complete"})
        resp, jm, job_ref = self._restart(client, job, {"preserve_audio_stems": True, "delete_outputs": False})
        assert resp.status_code == 200, resp.text
        payload = job_ref.update.call_args_list[0].args[0]
        assert payload["state_data.admin_rerender"] is DELETE_FIELD
        assert payload["status"] == "downloading"
        jm.update_job.assert_not_called()  # no separate pre-write

    def test_full_restart_clears_marker_atomically(self, client):
        resp, _, job_ref = self._restart(client, self._failed_job(),
                                         {"preserve_audio_stems": False, "delete_outputs": False})
        assert resp.status_code == 200, resp.text
        payload = job_ref.update.call_args_list[0].args[0]
        assert payload["state_data.admin_rerender"] is DELETE_FIELD


# --- R2#2: quiet re-publish vs. the community reconcile ------------------------------

# --- R2#3 + R2#6: queue processor vs admin re-render --------------------------------

# --- R2#4/#5: atomic marker clear + completion counting ------------------------------

class TestTransitionExtrasAndCounting:
    def _jm(self):
        from backend.services.job_manager import JobManager
        jm = JobManager.__new__(JobManager)
        jm.firestore = MagicMock()
        jm.validate_state_transition = MagicMock(return_value=True)
        jm.update_job_status = MagicMock()
        jm.get_job = MagicMock(return_value=SimpleNamespace(user_email="c@x.com"))
        jm._trigger_state_notifications = MagicMock()
        return jm

    def test_extra_updates_written_with_status(self):
        from backend.models.job import JobStatus
        jm = self._jm()
        jm.transition_to_state("job123", JobStatus.COMPLETE, progress=100,
                               extra_updates={"state_data.admin_rerender": DELETE_FIELD})
        jm.update_job_status.assert_called_once()
        assert jm.update_job_status.call_args.kwargs["state_data.admin_rerender"] is DELETE_FIELD

    def test_failed_transition_write_keeps_marker(self):
        """If the COMPLETE write raises, no separate marker deletion happened."""
        from backend.models.job import JobStatus
        jm = self._jm()
        jm.update_job_status.side_effect = RuntimeError("firestore down")
        with pytest.raises(RuntimeError):
            jm.transition_to_state("job123", JobStatus.COMPLETE,
                                   extra_updates={"state_data.admin_rerender": DELETE_FIELD})
        jm.firestore.update_job.assert_not_called()

    @pytest.mark.parametrize("count, expected", [(True, 1), (False, 0)])
    def test_count_completion(self, count, expected):
        from backend.models.job import JobStatus
        jm = self._jm()
        user_service = MagicMock()
        with patch("backend.services.user_service.get_user_service", return_value=user_service):
            jm.transition_to_state("job123", JobStatus.COMPLETE, count_completion=count)
        assert user_service.increment_jobs_completed.call_count == expected

    @pytest.mark.asyncio
    @pytest.mark.parametrize("state, expected", [
        ({"instrumental_selection": "clean", "admin_rerender": {"brand_code": "X", "notify_customer": True}}, False),
        ({"instrumental_selection": "clean", "admin_rerender": {"brand_code": "X"}}, False),
        ({"instrumental_selection": "clean", "theme_rerender": {"theme_id": "t"}}, True),
        ({"instrumental_selection": "clean"}, True),
    ])
    async def test_video_worker_counts_only_real_completions(self, state, expected):
        jm = await _run_video_worker(state)
        assert _complete_call(jm).kwargs["count_completion"] is expected


# --- R2#7: deferred YouTube upload kept when YouTube isn't re-published ----------------

# --- R2#8: legacy path Discord gating + kept brand code ---------------------------------

class TestLegacyDiscordAndBrandCode:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("marker, expected_webhook", [
        ({"brand_code": "NOMAD-1234"}, None),
        ({"brand_code": "NOMAD-1234", "notify_customer": True}, "https://discord/hook"),
        (None, "https://discord/hook"),
    ])
    async def test_karaoke_finalise_discord_gated(self, marker, expected_webhook):
        from backend.workers import video_worker

        job = MagicMock()
        job.job_id = "job123"
        job.artist = "A"
        job.title = "T"
        job.review_token = None
        job.edit_count = 0
        job.discord_webhook_url = "https://discord/hook"
        job.enable_youtube_upload = False
        job.is_private = False
        job.organised_dir_rclone_root = None
        job.existing_instrumental_gcs_path = None
        job.state_data = {"instrumental_selection": "clean"}
        if marker:
            job.state_data["admin_rerender"] = marker
        job_manager = MagicMock()
        job_manager.get_job.return_value = job
        finalise = MagicMock()
        finalise.return_value.process.return_value = {"brand_code": None}
        with patch.object(video_worker, "JobManager", return_value=job_manager), \
             patch.object(video_worker, "StorageService", return_value=MagicMock()), \
             patch.object(video_worker, "create_job_logger", return_value=MagicMock()), \
             patch.object(video_worker, "setup_job_logging", return_value=MagicMock()), \
             patch.object(video_worker, "_validate_prerequisites", return_value=True), \
             patch("backend.services.job_health_service.validate_worker_can_run", return_value=None), \
             patch.object(video_worker, "_setup_working_directory", new=AsyncMock()), \
             patch.object(video_worker, "load_style_config", new=AsyncMock(return_value=MagicMock())), \
             patch.object(video_worker, "get_encoding_service", return_value=MagicMock(is_enabled=False)), \
             patch.object(video_worker, "KaraokeFinalise", finalise), \
             patch.object(video_worker, "_handle_native_distribution", new=AsyncMock()), \
             patch.object(video_worker, "_upload_results", new=AsyncMock()), \
             patch.object(video_worker.os, "chdir"), \
             patch("backend.services.community_publish.notify_community_publish", new=AsyncMock()):
            await video_worker.generate_video_legacy("job123")
        finalise.assert_called_once()
        assert finalise.call_args.kwargs["discord_webhook_url"] == expected_webhook

    @pytest.mark.asyncio
    async def test_native_distribution_uses_kept_brand_code(self):
        """Both paths distribute via _handle_native_distribution, which keeps the code."""
        from backend.workers import video_worker

        job = _job(review_token=None, state_data={"admin_rerender": {"brand_code": "NOMAD-1234"}})
        job.keep_brand_code = None
        result = {"brand_code": None}
        dist = SimpleNamespace(dropbox_path=None, brand_prefix=None, gdrive_folder_id=None,
                               enable_youtube_upload=False)
        with patch("backend.services.job_defaults_service.get_effective_distribution_for_job", return_value=dist):
            await video_worker._handle_native_distribution(
                job_id="job123", job=job, job_log=MagicMock(), job_manager=MagicMock(),
                temp_dir="/tmp/x", result=result, storage=None,
            )
        assert result["brand_code"] == "NOMAD-1234"


# --- R2#9: admin delete-outputs uses the shared helpers ---------------------------------

class TestDeleteOutputsSharedHelpers:
    def test_sanitised_dropbox_path_and_helpers(self, client):
        from backend.api.routes import admin as admin_routes
        from karaoke_gen.utils import sanitize_filename
        test_client, app = client
        _use_auth(app, _auth())
        artist, title = "AC/DC", 'What’s "Up"?'
        job = _job(artist=artist, title=title, status="complete")
        job_manager = MagicMock()
        job_manager.get_job.return_value = job
        dropbox = MagicMock(is_configured=True)
        dropbox.delete_folder.return_value = True
        dropbox.file_exists.side_effect = lambda path: "AC_DC" in path  # sanitised folder only
        yt = MagicMock(return_value={"status": "success", "video_id": "abc123"})
        gd = MagicMock(return_value={"status": "success", "files": {}})
        with patch.object(admin_routes, "JobManager", return_value=job_manager), \
             patch.object(admin_routes, "get_user_service"), \
             patch.object(admin_routes, "log_to_job"), \
             patch("backend.services.dropbox_service.get_dropbox_service", return_value=dropbox), \
             patch("backend.services.published_outputs_cleanup.delete_youtube_video", yt), \
             patch("backend.services.published_outputs_cleanup.delete_gdrive_files", gd), \
             patch("backend.services.nomad_master_mirror.cleanup_nomad_masters") as mirror, \
             patch("backend.services.storage_service.StorageService"), \
             patch("backend.services.brand_code_service.get_brand_code_service"):
            resp = test_client.post("/api/admin/jobs/job123/delete-outputs")
        assert resp.status_code == 200, resp.text
        expected = f"/Karaoke/Tracks-Organized/NOMAD-1234 - {sanitize_filename(artist)} - {sanitize_filename(title)}"
        dropbox.delete_folder.assert_called_once_with(expected)
        yt.assert_called_once_with("job123", "https://www.youtube.com/watch?v=abc123")
        gd.assert_called_once_with("job123", {"mp4": "g1", "mp4_720p": "g2", "cdg": "g3"}, "NOMAD-1234",
                                   cleanup_mirror=False)
        mirror.assert_called_once_with("NOMAD-1234")
        assert resp.json()["deleted_services"]["dropbox"]["path"] == expected


# --- R2#11: active marker only, CANCELLED resumable ----------------------------------------

class TestActiveMarkerInClaimAndCancelled:
    @pytest.mark.asyncio
    async def test_stale_marker_does_not_leak_into_new_run(self, deps):
        service, _, worker_service = _service()
        job = _job(review_token="tok-new", state_data={
            "instrumental_selection": "clean",
            "admin_rerender": {"brand_code": "NOMAD-OLD", "review_token": "tok-old",
                               "previous_outputs": {"youtube_url": "https://youtu.be/ancient"}},
        })
        result = await _start(service, worker_service, job)
        marker = _claim_update(service)["state_data.admin_rerender"]
        assert result["brand_code"] is None
        assert marker["brand_code"] is None
        assert "youtube_url" not in marker["previous_outputs"]
        assert _claim_update(service)["timeline"].values[0]["metadata"]["retry"] is False

    def test_cancelled_admin_rerender_is_resumable(self):
        job = _job(status="cancelled", state_data={"instrumental_selection": "clean",
                                                    "admin_rerender": {"brand_code": "X"}})
        assert validate_admin_rerender(job) is None

    def test_cancelled_ordinary_job_is_not(self):
        assert "Only completed jobs" in validate_admin_rerender(_job(status="cancelled"))

    @pytest.mark.asyncio
    async def test_claims_cancelled(self, deps):
        service, _, worker_service = _service(claim_status="cancelled")
        job = _job(status="cancelled", state_data={"instrumental_selection": "clean",
                                                    "admin_rerender": {"brand_code": "X"}})
        await _start(service, worker_service, job)
        worker_service.trigger_screens_worker.assert_awaited_once()

    def test_retry_endpoint_resumes_cancelled_admin_rerender(self, client):
        test_client, app = client
        _use_auth(app, _auth())
        job = _job(status="cancelled", state_data={"instrumental_selection": "clean",
                                                    "admin_rerender": {"brand_code": "X"}})
        job.error_details = None
        job_manager = MagicMock()
        job_manager.get_job.return_value = job
        start = AsyncMock()
        with patch("backend.api.routes.jobs.job_manager", job_manager), \
             patch("backend.api.routes.jobs.JobManager", return_value=job_manager), \
             patch.object(AdminRerenderService, "start", start):
            resp = test_client.post("/api/jobs/job123/retry")
        assert resp.status_code == 200, resp.text
        assert resp.json()["retry_stage"] == "admin_rerender"


# =============================================================================
# Final review round
# =============================================================================

# --- A: re-render claim and queue claim are mutually exclusive ------------------------

class TestClaimReadsYouTubeQueue:
    @pytest.mark.asyncio
    async def test_claim_refused_while_upload_processing(self, deps):
        service, storage, worker_service = _service(queue_status="processing")
        with pytest.raises(RerenderConflictError) as exc:
            await _start(service, worker_service, _job())
        assert exc.value.status_code == 409
        assert "YouTube upload for this track is in progress" in str(exc.value)
        assert _job_updates(service) == [] and _queue_updates(service) == []
        deps.youtube.assert_not_called()
        storage.delete_file.assert_not_called()
        worker_service.trigger_screens_worker.assert_not_awaited()

    def test_route_maps_upload_in_progress_to_409(self, client):
        from backend.services.admin_rerender_service import UPLOAD_IN_PROGRESS_MESSAGE
        start = AsyncMock(side_effect=RerenderConflictError(UPLOAD_IN_PROGRESS_MESSAGE))
        resp, _ = _post(client, _auth(), _job(), start=start)
        assert resp.status_code == 409
        assert "try again in a few minutes" in resp.json()["detail"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("queue_status", ["queued", "failed"])
    async def test_claim_cancels_pending_entry_in_same_transaction(self, deps, queue_status):
        service, _, worker_service = _service(queue_status=queue_status)
        result = await _start(service, worker_service, _job())
        transaction = service.job_manager.firestore.db.transaction.return_value
        # both writes go through the one claim transaction
        assert len(transaction.update.call_args_list) == 2
        assert _queue_updates(service)[0]["status"] == "cancelled"
        assert _claim_update(service)["state_data.youtube_upload_queued"] is DELETE_FIELD
        assert result["cleanup_results"]["youtube_queue"] == {"status": "cancelled", "previous_status": queue_status}

    @pytest.mark.asyncio
    async def test_no_queue_entry(self, deps):
        service, _, worker_service = _service()
        result = await _start(service, worker_service, _job())
        assert _queue_updates(service) == []
        assert result["cleanup_results"]["youtube_queue"]["status"] == "skipped"

    @pytest.mark.asyncio
    async def test_entry_kept_with_warning_when_youtube_not_republished(self, deps):
        deps.plan.return_value = {"youtube": (False, "YouTube upload is disabled for this job"),
                                  "dropbox": (True, None), "gdrive": (True, None)}
        service, _, worker_service = _service(queue_status="queued")
        job = _job(state_data={**_job().state_data, "youtube_upload_queued": True})
        result = await _start(service, worker_service, job)
        assert _queue_updates(service) == []
        update = _claim_update(service)
        assert "state_data.youtube_upload_queued" not in update
        assert "state_data.youtube_url" not in update
        warning = next(w for w in result["warnings"] if "deferred YouTube upload left queued" in w)
        assert warning in update["state_data.admin_rerender"]["warnings"]
        assert result["cleanup_results"]["youtube_queue"]["status"] == "kept"

    @pytest.mark.asyncio
    async def test_credential_check_error_leaves_youtube_and_queue_alone(self, deps):
        deps.plan.side_effect = _REAL_PLAN_REPUBLISH
        service, _, worker_service = _service(queue_status="queued")
        with patch("backend.services.youtube_service.get_youtube_service", side_effect=RuntimeError("boom")):
            result = await _start(service, worker_service, _job())
        deps.youtube.assert_not_called()
        assert _queue_updates(service) == []
        assert any("couldn't verify YouTube credentials" in w for w in result["warnings"])


def _queue_service_with(job_doc, queue_status="queued"):
    """Real YouTubeUploadQueueService over a fake db holding one queue doc + one job doc."""
    from backend.services.youtube_upload_queue_service import YouTubeUploadQueueService
    db = MagicMock()
    refs = {}
    for name, data in (("youtube_upload_queue", {"status": queue_status, "attempts": 0, "max_attempts": 5}),
                       ("jobs", job_doc)):
        ref = MagicMock(name=name)
        snap = MagicMock(exists=data is not None)
        snap.to_dict.return_value = data
        ref.get.return_value = snap
        refs[name] = ref
    db.collection.side_effect = lambda name: MagicMock(**{"document.return_value": refs[name]})
    return YouTubeUploadQueueService(db=db), db, refs


class TestQueueClaimReadsJob:
    def _claim(self, job_doc, queue_status="queued"):
        service, db, refs = _queue_service_with(job_doc, queue_status)
        with patch("backend.services.youtube_upload_queue_service.firestore.transactional", lambda fn: fn):
            ok = service.mark_processing("job123")
        updates = db.transaction.return_value.update.call_args_list
        return ok, updates, refs

    def test_refuses_while_admin_rerender_active(self):
        ok, updates, refs = self._claim({"status": "rendering_video", "review_token": "t",
                                         "state_data": {"admin_rerender": {"review_token": "t"}}})
        assert ok is False and updates == []
        refs["jobs"].get.assert_called_once()  # read inside the transaction

    def test_refuses_while_admin_rerender_failed(self):
        ok, updates, _ = self._claim({"status": "failed", "state_data": {"admin_rerender": {"brand_code": "X"}}})
        assert ok is False and updates == []

    @pytest.mark.parametrize("job_doc", [
        {"status": "complete", "state_data": {}},                                   # normal job
        {"status": "complete", "state_data": {"admin_rerender": {"brand_code": "X"}}},  # re-render done
        {"status": "rendering_video", "review_token": "new",                        # stale marker
         "state_data": {"admin_rerender": {"review_token": "old"}}},
        None,                                                                       # job doc missing
    ])
    def test_claims_otherwise(self, job_doc):
        ok, updates, _ = self._claim(job_doc)
        assert ok is True
        assert updates[0].args[1]["status"] == "processing"
        assert updates[0].args[1]["attempts"] == 1

    def test_not_queued_still_refused(self):
        ok, updates, _ = self._claim({"status": "complete", "state_data": {}}, queue_status="completed")
        assert ok is False and updates == []

    def test_cancel_upload_removed(self):
        """Nothing outside the re-render's claim transaction cancels queue entries,
        so the re-render's own later queue_upload entry can't be cancelled."""
        from backend.services.youtube_upload_queue_service import YouTubeUploadQueueService
        assert not hasattr(YouTubeUploadQueueService, "cancel_upload")


def _snapshot(job_id, data):
    snap = MagicMock(exists=data is not None, id=job_id)
    snap.to_dict.return_value = data
    return snap


def _q_entry(job_id, **extra):
    return {"job_id": job_id, "user_email": "c@example.com", "artist": "A", "title": "T",
            "brand_code": "NOMAD-1234", **extra}


async def _run_queue(entries, job_docs=None, claimable=None, upload=None, update_url=None, community=None):
    from backend.workers import youtube_queue_processor as qp

    queue_service = MagicMock()
    queue_service.get_queued_uploads.side_effect = [entries, []]
    claimable = claimable if claimable is not None else {e["job_id"] for e in entries}
    queue_service.mark_processing.side_effect = lambda job_id: job_id in claimable
    job_docs = job_docs or {}
    queue_service.db.get_all.return_value = [
        _snapshot(job_id, data) for job_id, data in job_docs.items()
    ]
    quota_service = MagicMock()
    quota_service.check_quota_available.return_value = (True, 10000, "ok")
    upload = upload or AsyncMock(return_value="https://youtu.be/new")
    send = AsyncMock()
    community = community or AsyncMock()
    update_url = update_url or MagicMock()
    job_manager_cls = MagicMock()
    with patch.object(qp, "get_youtube_upload_queue_service", return_value=queue_service), \
         patch.object(qp, "get_youtube_quota_service", return_value=quota_service), \
         patch.object(qp, "JobManager", job_manager_cls), \
         patch.object(qp, "_process_single_upload", new=upload), \
         patch.object(qp, "_update_job_youtube_url", update_url), \
         patch.object(qp, "_send_youtube_upload_notification", new=send), \
         patch.object(qp, "notify_community_publish", new=community):
        summary = await qp.process_youtube_upload_queue()
    return SimpleNamespace(queue=queue_service, upload=upload, send=send, community=community,
                           update_url=update_url, summary=summary, job_manager_cls=job_manager_cls)


class TestQueueProcessorFlow:
    @pytest.mark.asyncio
    async def test_normal_flow_unchanged(self):
        r = await _run_queue([_q_entry("job123")], {"job123": {"status": "complete", "state_data": {}}})
        r.queue.mark_processing.assert_called_once_with("job123")
        r.queue.mark_completed.assert_called_once_with("job123", "https://youtu.be/new")
        r.update_url.assert_called_once_with("job123", "https://youtu.be/new")
        r.send.assert_awaited_once()
        r.community.assert_awaited_once_with("job123", "https://youtu.be/new")
        r.queue.mark_failed.assert_not_called()
        assert r.summary["processed"] == 1

    @pytest.mark.asyncio
    async def test_refused_claim_leaves_entry_and_does_not_starve(self):
        from backend.workers import youtube_queue_processor as qp
        entries = [_q_entry(f"busy{i}") for i in range(30)] + [_q_entry("ok")]
        r = await _run_queue(entries, claimable={"ok"})
        assert r.queue.get_queued_uploads.call_args_list[0].kwargs["limit"] == qp.QUEUE_FETCH_LIMIT
        assert [c.args[0] for c in r.upload.await_args_list] == ["ok"]
        r.queue.mark_failed.assert_not_called()

    @pytest.mark.asyncio
    async def test_upload_attempts_capped(self):
        from backend.workers import youtube_queue_processor as qp
        entries = [_q_entry(f"j{i}") for i in range(qp.MAX_UPLOADS_PER_RUN + 5)]
        r = await _run_queue(entries)
        assert r.upload.await_count == qp.MAX_UPLOADS_PER_RUN

    @pytest.mark.asyncio
    async def test_rerender_own_entry_uploads_without_email(self):
        """The re-render's own queued upload (notify_user=False) is processed, never cancelled."""
        r = await _run_queue([_q_entry("job123", notify_user=False)],
                             {"job123": {"status": "complete", "state_data": {}}})
        r.upload.assert_awaited_once()
        r.queue.mark_completed.assert_called_once()
        r.send.assert_not_awaited()
        assert not any("cancel" in name for name, *_ in r.queue.mock_calls)

    @pytest.mark.asyncio
    async def test_silent_marker_suppresses_email(self):
        r = await _run_queue([_q_entry("job123")], {"job123": {
            "status": "complete", "state_data": {"admin_rerender": {"brand_code": "X"}}}})
        r.send.assert_not_awaited()

    # --- G: batched job reads ---
    @pytest.mark.asyncio
    async def test_jobs_read_in_one_batch(self):
        entries = [_q_entry("a"), _q_entry("b"), _q_entry("a")]
        r = await _run_queue(entries, {"a": {"status": "complete", "state_data": {}},
                                       "b": {"status": "complete", "state_data": {}}})
        r.queue.db.get_all.assert_called_once()
        assert len(r.queue.db.get_all.call_args.args[0]) == 2  # de-duplicated ids
        r.job_manager_cls.return_value.get_job.assert_not_called()

    def test_batch_helper_builds_views_and_survives_errors(self):
        from backend.workers.youtube_queue_processor import _get_jobs_batch
        db = MagicMock()
        db.get_all.return_value = [_snapshot("a", {"status": "complete", "review_token": "t",
                                                   "state_data": {"x": 1}}),
                                   _snapshot("gone", None)]
        jobs = _get_jobs_batch(db, ["a", "gone"])
        assert set(jobs) == {"a"}
        assert jobs["a"].status == "complete" and jobs["a"].review_token == "t"
        assert jobs["a"].state_data == {"x": 1}
        db.get_all.side_effect = RuntimeError("down")
        assert _get_jobs_batch(db, ["a"]) == {}
        assert _get_jobs_batch(db, []) == {}

    # --- B: never re-queue after a successful upload ---
    @pytest.mark.asyncio
    async def test_post_upload_error_never_requeues(self):
        r = await _run_queue([_q_entry("job123")], {"job123": {"status": "complete", "state_data": {}}},
                             update_url=MagicMock(side_effect=RuntimeError("firestore down")))
        r.queue.mark_completed.assert_called_once_with("job123", "https://youtu.be/new")
        r.queue.mark_failed.assert_not_called()
        r.queue.mark_post_upload_error.assert_called_once()
        assert r.queue.mark_post_upload_error.call_args.args[:2] == ("job123", "https://youtu.be/new")

    @pytest.mark.asyncio
    async def test_mark_completed_failure_never_requeues(self):
        from backend.workers import youtube_queue_processor as qp
        entries = [_q_entry("job123")]
        queue_service = MagicMock()
        queue_service.get_queued_uploads.side_effect = [entries, []]
        queue_service.mark_processing.return_value = True
        queue_service.mark_completed.side_effect = RuntimeError("write failed")
        queue_service.db.get_all.return_value = []
        quota = MagicMock()
        quota.check_quota_available.return_value = (True, 1, "ok")
        with patch.object(qp, "get_youtube_upload_queue_service", return_value=queue_service), \
             patch.object(qp, "get_youtube_quota_service", return_value=quota), \
             patch.object(qp, "_process_single_upload", new=AsyncMock(return_value="https://youtu.be/new")), \
             patch.object(qp, "_update_job_youtube_url"), \
             patch.object(qp, "_send_youtube_upload_notification", new=AsyncMock()), \
             patch.object(qp, "notify_community_publish", new=AsyncMock()):
            await qp.process_youtube_upload_queue()
        queue_service.mark_failed.assert_not_called()
        queue_service.mark_post_upload_error.assert_called_once()

    @pytest.mark.asyncio
    async def test_real_url_write_failure_flags_needs_attention(self):
        """End to end through the REAL _update_job_youtube_url (not a raising mock):
        a failed job write must reach mark_post_upload_error."""
        from backend.workers import youtube_queue_processor as qp
        entries = [_q_entry("job123")]
        queue_service = MagicMock()
        queue_service.get_queued_uploads.side_effect = [entries, []]
        queue_service.mark_processing.return_value = True
        queue_service.db.get_all.return_value = []
        quota = MagicMock()
        quota.check_quota_available.return_value = (True, 1, "ok")
        job_manager = MagicMock()
        job_manager.get_job.return_value = MagicMock(state_data={})
        job_manager.update_job.side_effect = RuntimeError("firestore down")
        with patch.object(qp, "get_youtube_upload_queue_service", return_value=queue_service), \
             patch.object(qp, "get_youtube_quota_service", return_value=quota), \
             patch.object(qp, "_process_single_upload", new=AsyncMock(return_value="https://youtu.be/new")), \
             patch.object(qp, "JobManager", return_value=job_manager), \
             patch.object(qp, "_send_youtube_upload_notification", new=AsyncMock()), \
             patch.object(qp, "notify_community_publish", new=AsyncMock()):
            await qp.process_youtube_upload_queue()
        queue_service.mark_completed.assert_called_once_with("job123", "https://youtu.be/new")
        queue_service.mark_failed.assert_not_called()
        queue_service.mark_post_upload_error.assert_called_once()

    def test_claim_reads_configured_jobs_collection(self):
        """The re-render claim must contend on the same job document the queue's
        mark_processing transaction reads (settings.firestore_collection)."""
        from backend.services import admin_rerender_service as ars
        db = MagicMock()
        with patch("backend.config.get_settings", return_value=MagicMock(firestore_collection="jobs-test")), \
             patch.object(ars.firestore, "transactional",
                          side_effect=lambda f: (lambda tx: ("ok", {"status": "none"}))):
            outcome = ars.claim_with_youtube_queue(db, "job123", {}, {"complete"}, cancel_deferred_upload=False)
        assert outcome == {"status": "none"}
        db.collection.assert_any_call("jobs-test")

    @pytest.mark.asyncio
    async def test_community_failure_after_upload_never_requeues(self):
        r = await _run_queue([_q_entry("job123")], {"job123": {"status": "complete", "state_data": {}}},
                             community=AsyncMock(side_effect=RuntimeError("song requests down")))
        r.queue.mark_failed.assert_not_called()
        r.queue.mark_post_upload_error.assert_called_once()

    @pytest.mark.asyncio
    async def test_upload_failure_still_requeues(self):
        r = await _run_queue([_q_entry("job123")], {"job123": {"status": "complete", "state_data": {}}},
                             upload=AsyncMock(side_effect=RuntimeError("network")))
        r.queue.mark_failed.assert_called_once_with("job123", "network")
        r.queue.mark_post_upload_error.assert_not_called()

    def test_mark_post_upload_error_is_terminal(self):
        from backend.services.youtube_upload_queue_service import YouTubeUploadQueueService
        db = MagicMock()
        YouTubeUploadQueueService(db=db).mark_post_upload_error("job123", "https://youtu.be/x", "boom")
        payload = db.collection.return_value.document.return_value.update.call_args.args[0]
        assert payload["status"] == "completed" and payload["needs_attention"] is True
        assert payload["youtube_url"] == "https://youtu.be/x"


# --- C: community voters are owed "it's live"; only re-notification is suppressed ------

class _FakeRequests:
    def __init__(self, requests, upvoters):
        self.requests = {r.id: r for r in requests}
        self.upvoters = upvoters

    def get_by_job_id(self, job_id):
        return next((r for r in self.requests.values() if r.job_id == job_id), None)

    def mark_published(self, rid, url):
        self.requests[rid].status, self.requests[rid].youtube_url = "published", url

    def mark_voters_notified(self, rid):
        self.requests[rid].voters_notified = True

    def add_notified_voters(self, rid, emails):
        self.requests[rid].notified_voters = [*self.requests[rid].notified_voters, *emails]

    def list_upvoters(self, rid):
        return list(self.upvoters.get(rid, []))

    def list_in_progress(self):
        return [r for r in self.requests.values() if r.status == "in_progress"]

    def list_published_unnotified(self):
        return [r for r in self.requests.values() if r.status == "published" and not r.voters_notified]


def _req(rid, job_id, **kw):
    fields = dict(id=rid, job_id=job_id, status="published", youtube_url="https://youtu.be/old",
                  voters_notified=False, notified_voters=[], owner_email="owner@x.com",
                  submitted_by=None, artist="A", title="T")
    fields.update(kw)
    return SimpleNamespace(**fields)


class TestCommunityVotersOnQuietRepublish:
    async def _go(self, service, coro):
        notifier = MagicMock()
        notifier.send_community_track_live_email = AsyncMock(return_value=True)
        with patch("backend.services.song_request_service.get_song_request_service", return_value=service), \
             patch("backend.services.job_notification_service.get_job_notification_service", return_value=notifier), \
             patch("backend.services.job_manager.JobManager"):
            await coro()
        return notifier

    @pytest.mark.asyncio
    async def test_already_notified_voters_are_not_re_emailed(self):
        from backend.services.community_publish import notify_community_publish, reconcile_community_publishes
        svc = _FakeRequests([_req("r1", "job123", voters_notified=True, notified_voters=["v@x.com"])],
                            {"r1": ["v@x.com"]})

        async def go():
            await notify_community_publish("job123", "https://youtu.be/new")
            await reconcile_community_publishes()

        notifier = await self._go(svc, go)
        notifier.send_community_track_live_email.assert_not_awaited()
        assert svc.requests["r1"].youtube_url == "https://youtu.be/new"

    @pytest.mark.asyncio
    async def test_owed_fanout_is_completed_by_quiet_republish(self):
        """Partially notified before: the remaining voters are still owed 'it's live'."""
        from backend.services.community_publish import notify_community_publish
        svc = _FakeRequests([_req("r1", "job123", notified_voters=["a@x.com"])],
                            {"r1": ["a@x.com", "b@x.com"]})
        notifier = await self._go(svc, lambda: notify_community_publish("job123", "https://youtu.be/new"))
        sent_to = [c.kwargs["to_email"] for c in notifier.send_community_track_live_email.await_args_list]
        assert sent_to == ["b@x.com"]
        assert svc.requests["r1"].voters_notified is True  # only because it actually completed

    @pytest.mark.asyncio
    async def test_failed_send_leaves_request_unnotified_for_reconcile(self):
        from backend.services.community_publish import notify_community_publish, reconcile_community_publishes
        svc = _FakeRequests([_req("r1", "job123")], {"r1": ["b@x.com"]})
        notifier = MagicMock()
        notifier.send_community_track_live_email = AsyncMock(side_effect=[False, True])
        with patch("backend.services.song_request_service.get_song_request_service", return_value=svc), \
             patch("backend.services.job_notification_service.get_job_notification_service", return_value=notifier), \
             patch("backend.services.job_manager.JobManager"):
            await notify_community_publish("job123", "https://youtu.be/new")
            assert svc.requests["r1"].voters_notified is False
            await reconcile_community_publishes()
        assert svc.requests["r1"].voters_notified is True
        assert notifier.send_community_track_live_email.await_count == 2

    @pytest.mark.asyncio
    async def test_video_worker_calls_plain_community_publish(self):
        community = AsyncMock()
        await _run_video_worker({"instrumental_selection": "clean",
                                 "admin_rerender": {"brand_code": "NOMAD-1234"}}, community=community)
        community.assert_awaited_once_with("job123", "https://www.youtube.com/watch?v=new456")


# --- D: conflicting state_data paths --------------------------------------------------

class TestTransitionStateDataConflict:
    def test_both_state_data_forms_rejected(self):
        from backend.models.job import JobStatus
        from backend.services.job_manager import JobManager
        jm = JobManager.__new__(JobManager)
        jm.firestore = MagicMock()
        jm.validate_state_transition = MagicMock(return_value=True)
        jm.update_job_status = MagicMock()
        with pytest.raises(ValueError, match="state_data_updates OR"):
            jm.transition_to_state("job123", JobStatus.COMPLETE, state_data_updates={"a": 1},
                                   extra_updates={"state_data.admin_rerender": DELETE_FIELD})
        jm.update_job_status.assert_not_called()

    def test_non_state_data_extras_combine_fine(self):
        from backend.models.job import JobStatus
        from backend.services.job_manager import JobManager
        jm = JobManager.__new__(JobManager)
        jm.firestore = MagicMock()
        jm.validate_state_transition = MagicMock(return_value=True)
        jm.update_job_status = MagicMock()
        jm.get_job = MagicMock(return_value=SimpleNamespace(state_data={"x": 1}, user_email=None))
        jm._trigger_state_notifications = MagicMock()
        jm.transition_to_state("job123", JobStatus.COMPLETE, state_data_updates={"a": 1},
                               extra_updates={"outputs_deleted_at": None}, count_completion=False)
        kwargs = jm.update_job_status.call_args.kwargs
        assert kwargs["state_data"] == {"x": 1, "a": 1} and kwargs["outputs_deleted_at"] is None


# --- E: legacy raw-name Dropbox folders ---------------------------------------------------

class TestDropboxLegacyFolderName:
    ARTIST, TITLE = "Beyoncé", "Love On Top: Live — “Encore”"

    def _delete(self, existing, failing=()):
        from backend.services.published_outputs_cleanup import (
            delete_dropbox_folder, dropbox_folder_path, legacy_dropbox_folder_path)
        sanitised = dropbox_folder_path("/K", "NOMAD-1", self.ARTIST, self.TITLE)
        legacy = legacy_dropbox_folder_path("/K", "NOMAD-1", self.ARTIST, self.TITLE)
        assert sanitised != legacy
        names = {"sanitised": sanitised, "legacy": legacy}
        dropbox = MagicMock(is_configured=True)
        dropbox.file_exists.side_effect = lambda p: p in {names[n] for n in existing}
        dropbox.delete_folder.side_effect = lambda p: p not in {names[n] for n in failing}
        with patch("backend.services.dropbox_service.get_dropbox_service", return_value=dropbox):
            return delete_dropbox_folder("job123", "/K", "NOMAD-1", self.ARTIST, self.TITLE), names, dropbox

    def test_only_legacy_folder_exists(self):
        result, names, dropbox = self._delete({"legacy"})
        assert result["status"] == "success"
        assert result["deleted"] == [names["legacy"]]
        dropbox.delete_folder.assert_called_once_with(names["legacy"])

    def test_both_exist(self):
        result, names, _ = self._delete({"sanitised", "legacy"})
        assert result["status"] == "success"
        assert result["deleted"] == [names["sanitised"], names["legacy"]]

    def test_neither_exists(self):
        result, _, dropbox = self._delete(set())
        assert result["status"] == "success" and result["deleted"] == []
        dropbox.delete_folder.assert_not_called()

    def test_failure_on_one_is_not_success(self):
        result, names, _ = self._delete({"sanitised", "legacy"}, failing={"legacy"})
        assert result["status"] == "failed"
        assert result["failed"] == [names["legacy"]]
        assert result["deleted"] == [names["sanitised"]]

    def test_existence_check_error_still_attempts_delete(self):
        from backend.services.published_outputs_cleanup import delete_dropbox_folder
        dropbox = MagicMock(is_configured=True)
        dropbox.file_exists.side_effect = RuntimeError("rate limited")
        dropbox.delete_folder.return_value = True
        with patch("backend.services.dropbox_service.get_dropbox_service", return_value=dropbox):
            result = delete_dropbox_folder("job123", "/K", "NOMAD-1", self.ARTIST, self.TITLE)
        assert result["status"] == "success" and dropbox.delete_folder.call_count == 2


# --- F: YouTube helper never raises -----------------------------------------------------

class TestYouTubeHelperNeverRaises:
    @pytest.mark.parametrize("bad_url", [12345, ["https://youtu.be/x"], {"url": "x"}])
    def test_non_string_url(self, bad_url):
        from backend.services.published_outputs_cleanup import delete_youtube_video
        result = delete_youtube_video("job123", bad_url)
        assert result["status"] == "error"
