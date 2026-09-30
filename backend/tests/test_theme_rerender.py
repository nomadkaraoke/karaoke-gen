"""
Tests for re-rendering a finished tenant track with the tenant's current theme.

Covers validation, the service (theme re-snapshot, atomic claim, artifact
cleanup, screens-worker trigger + rollback), the POST /api/jobs/{id}/rerender
route (ownership, tenant allowlist), and the encoding-worker id keying that
stops a re-run from getting the previous run's cached render/encode back.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from google.cloud.firestore_v1 import DELETE_FIELD

from backend.services.theme_rerender_service import (
    RerenderConflictError,
    RerenderError,
    ThemeRerenderService,
    validate_rerender,
)
from backend.workers.supersede import encoding_worker_job_id


def _job(**overrides):
    fields = dict(
        job_id="job123",
        status="complete",
        tenant_id="randy-vild",
        user_email="randy@example.com",
        theme_id="randy-vild",
        color_overrides={},
        outputs_deleted_at=None,
        prep_only=False,
        finalise_only=False,
        state_data={"instrumental_selection": "custom"},
        file_urls={"lyrics": {"corrections": "jobs/job123/lyrics/corrections.json"}},
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


class TestValidateRerender:
    def test_valid_tenant_job(self):
        assert validate_rerender(_job()) is None

    @pytest.mark.parametrize("overrides, fragment", [
        ({"tenant_id": None}, "portal tracks"),
        ({"status": "rendering_video"}, "Only finished tracks"),
        ({"outputs_deleted_at": "2026-09-29T18:47:13Z"}, "outputs were deleted"),
        ({"theme_id": None}, "wasn't made from a theme"),
        ({"prep_only": True}, "can't be re-rendered"),
        ({"state_data": {}}, "instrumental selection"),
        ({"file_urls": {}}, "reviewed lyrics"),
        ({"state_data": {"instrumental_selection": "clean", "dropbox_link": "https://db"}}, "published"),
        ({"state_data": {"instrumental_selection": "clean", "youtube_url": "https://yt"}}, "published"),
    ])
    def test_rejections(self, overrides, fragment):
        assert fragment in validate_rerender(_job(**overrides))


def _service(claim_status="complete", trigger_ok=True):
    """ThemeRerenderService wired to a fake Firestore transaction + storage."""
    job_manager = MagicMock()
    doc_ref = MagicMock()
    snapshot = MagicMock(exists=True)
    snapshot.to_dict.return_value = {"status": claim_status}
    doc_ref.get.return_value = snapshot
    job_manager.firestore.db.collection.return_value.document.return_value = doc_ref
    storage = MagicMock()
    worker_service = MagicMock()
    worker_service.trigger_screens_worker = AsyncMock(return_value=trigger_ok)
    return ThemeRerenderService(job_manager=job_manager, storage=storage), doc_ref, storage, worker_service


def _passthrough_transactional(fn):
    return fn


@pytest.fixture
def patched_deps():
    prepare = MagicMock(return_value=(
        "jobs/job123/style/style_params.json",
        {"karaoke_background": "themes/randy-vild/assets/bg-new.jpg"},
        None,
    ))
    with patch("backend.api.routes.file_upload._prepare_theme_for_job", prepare), \
         patch("backend.services.theme_rerender_service.firestore.transactional", _passthrough_transactional):
        yield prepare


class TestThemeRerenderService:
    @pytest.mark.asyncio
    async def test_resnapshots_theme_claims_and_triggers_screens(self, patched_deps):
        service, doc_ref, storage, worker_service = _service()
        transaction = service.job_manager.firestore.db.transaction.return_value

        with patch("backend.services.worker_service.get_worker_service", return_value=worker_service):
            await service.start(_job(), theme_id="randy-vild", requested_by="randy@example.com")

        patched_deps.assert_called_once_with("job123", "randy-vild", None)
        transaction.update.assert_called_once()
        update = transaction.update.call_args.args[1]
        assert update["status"] == "lyrics_complete"
        assert update["state_data.regen_restore_status"] == "review_complete"
        assert update["style_params_gcs_path"] == "jobs/job123/style/style_params.json"
        assert update["style_assets"] == {"karaoke_background": "themes/randy-vild/assets/bg-new.jpg"}
        assert update["theme_id"] == "randy-vild"
        for key in ("screens_progress", "render_progress", "video_progress", "encoding_progress"):
            assert update[f"state_data.{key}"] is DELETE_FIELD
        assert update["file_urls.screens"] is DELETE_FIELD
        assert update["file_urls.videos.with_vocals"] is DELETE_FIELD

        deleted = {c.args[0] for c in storage.delete_file.call_args_list}
        assert "jobs/job123/screens/title.mov" in deleted
        assert "jobs/job123/screens/end.png" in deleted
        assert "jobs/job123/videos/with_vocals.mkv" in deleted
        assert all(c.kwargs.get("ignore_missing") for c in storage.delete_file.call_args_list)

        worker_service.trigger_screens_worker.assert_awaited_once_with("job123")

    @pytest.mark.asyncio
    async def test_passes_color_overrides(self, patched_deps):
        service, _, _, worker_service = _service()
        with patch("backend.services.worker_service.get_worker_service", return_value=worker_service):
            await service.start(_job(color_overrides={"sung": "#fff"}), theme_id="t", requested_by="x")
        patched_deps.assert_called_once_with("job123", "t", {"sung": "#fff"})

    @pytest.mark.asyncio
    async def test_conflict_when_job_no_longer_complete(self, patched_deps):
        """A double-click: the second claim sees the job already re-rendering."""
        service, _, storage, worker_service = _service(claim_status="lyrics_complete")
        transaction = service.job_manager.firestore.db.transaction.return_value

        with patch("backend.services.worker_service.get_worker_service", return_value=worker_service):
            with pytest.raises(RerenderConflictError):
                await service.start(_job(), theme_id="randy-vild", requested_by="x")

        transaction.update.assert_not_called()
        storage.delete_file.assert_not_called()
        worker_service.trigger_screens_worker.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_restores_complete_when_trigger_fails(self, patched_deps):
        service, _, _, worker_service = _service(trigger_ok=False)

        with patch("backend.services.worker_service.get_worker_service", return_value=worker_service):
            with pytest.raises(RerenderError) as exc:
                await service.start(_job(), theme_id="randy-vild", requested_by="x")

        assert exc.value.status_code == 503
        restore = service.job_manager.update_job.call_args.args[1]
        assert restore["status"] == "complete"
        assert restore["state_data.regen_restore_status"] is DELETE_FIELD

    @pytest.mark.asyncio
    async def test_invalid_job_rejected_before_any_work(self, patched_deps):
        service, _, storage, _ = _service()
        with pytest.raises(RerenderError):
            await service.start(_job(tenant_id=None), theme_id="t", requested_by="x")
        patched_deps.assert_not_called()
        storage.delete_file.assert_not_called()


class TestEncodingWorkerJobId:
    def test_generation_zero_keeps_plain_job_id(self):
        assert encoding_worker_job_id("job123", 0) == "job123"

    def test_generation_suffixes_job_id(self):
        assert encoding_worker_job_id("job123", 3) == "job123_g3"

    def test_orchestrator_config_captures_generation(self):
        from backend.workers.video_worker_orchestrator import create_orchestrator_config_from_job

        job = MagicMock()
        job.job_id = "job123"
        job.artist = "A"
        job.title = "T"
        job.state_data = {"worker_generation": 4, "instrumental_selection": "clean"}
        job.file_urls = {}
        config = create_orchestrator_config_from_job(job, temp_dir="/tmp/x")
        assert config.worker_generation == 4


# --- Route ---------------------------------------------------------------

@pytest.fixture
def client():
    from backend.main import app
    yield TestClient(app), app
    app.dependency_overrides.clear()


def _auth(email="randy@example.com", is_admin=False):
    from backend.services.auth_service import AuthResult, UserType
    return AuthResult(
        is_valid=True,
        user_type=UserType.ADMIN if is_admin else UserType.UNLIMITED,
        remaining_uses=-1,
        message="Valid",
        user_email=email,
        is_admin=is_admin,
    )


def _call(client, auth, job, tenant, start=None):
    test_client, app = client
    from backend.api.dependencies import require_auth

    async def override():
        return auth

    app.dependency_overrides[require_auth] = override
    job_manager = MagicMock()
    job_manager.get_job.return_value = job
    tenant_service = MagicMock()
    tenant_service.get_tenant_config.return_value = tenant
    start = start or AsyncMock()
    with patch("backend.api.routes.jobs.JobManager", return_value=job_manager), \
         patch("backend.services.tenant_service.get_tenant_service", return_value=tenant_service), \
         patch.object(ThemeRerenderService, "start", start):
        return test_client.post(f"/api/jobs/{job.job_id}/rerender"), start


def _tenant(allowed=True, active=True):
    tenant = MagicMock()
    tenant.is_active = active
    tenant.is_email_allowed.return_value = allowed
    tenant.defaults.locked_theme = "randy-vild"
    tenant.defaults.theme_id = None
    tenant.id = "randy-vild"
    return tenant


class TestRerenderRoute:
    def test_owner_starts_rerender_with_tenant_theme(self, client):
        job = _job()
        resp, start = _call(client, _auth(), job, _tenant())
        assert resp.status_code == 200, resp.text
        assert resp.json()["theme_id"] == "randy-vild"
        start.assert_awaited_once()
        assert start.await_args.kwargs["theme_id"] == "randy-vild"
        assert start.await_args.kwargs["requested_by"] == "randy@example.com"

    def test_non_owner_forbidden(self, client):
        resp, start = _call(client, _auth(email="someone@else.com"), _job(), _tenant())
        assert resp.status_code == 403
        start.assert_not_awaited()

    def test_owner_removed_from_allowlist_forbidden(self, client):
        resp, start = _call(client, _auth(), _job(), _tenant(allowed=False))
        assert resp.status_code == 403
        start.assert_not_awaited()

    def test_admin_allowed_on_any_job(self, client):
        resp, start = _call(client, _auth(email="admin@nomadkaraoke.com", is_admin=True), _job(), _tenant(allowed=False))
        assert resp.status_code == 200
        start.assert_awaited_once()

    def test_consumer_job_rejected(self, client):
        resp, start = _call(client, _auth(), _job(tenant_id=None), _tenant())
        assert resp.status_code == 400
        start.assert_not_awaited()

    def test_inactive_tenant_rejected(self, client):
        resp, start = _call(client, _auth(), _job(), _tenant(active=False))
        assert resp.status_code == 400
        start.assert_not_awaited()

    def test_conflict_maps_to_409(self, client):
        start = AsyncMock(side_effect=RerenderConflictError("already"))
        resp, _ = _call(client, _auth(), _job(), _tenant(), start=start)
        assert resp.status_code == 409
