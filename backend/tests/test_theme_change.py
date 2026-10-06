"""
Tests for keeping a tenant's tracks in step with its theme after a theme edit.

Covers the in-progress refresh on save (re-snapshot + stale-screens flag), the
render-worker divert that regenerates stale screens before rendering, outdated
finished-track detection, Edit picking up the current theme, the quiet bulk
re-render, and the /api/tenant/theme save / outdated-jobs / rerender-outdated
routes.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from google.cloud.firestore_v1 import DELETE_FIELD

from backend.models.job import JobStatus
from backend.services import theme_change_service as svc

T0 = datetime(2026, 9, 29, 23, 0, tzinfo=timezone.utc)
THEME_SAVED = datetime(2026, 10, 6, 14, 35, tzinfo=timezone.utc)


def _job(job_id="job1", status=JobStatus.AWAITING_REVIEW, **overrides):
    fields = dict(
        job_id=job_id, status=status, tenant_id="randy-vild", theme_id="randy-vild",
        user_email="randy@example.com", color_overrides={}, created_at=T0,
        theme_applied_at=None, state_data={},
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _passthrough_transactional(fn):
    return fn


def _job_manager(claim_status=None):
    """JobManager whose Firestore transaction sees ``claim_status`` (None = job's own)."""
    jm = MagicMock()
    doc_ref = MagicMock()
    jm.firestore.db.collection.return_value.document.return_value = doc_ref
    jm._doc_ref = doc_ref
    jm._claim_status = claim_status
    return jm


def _set_claim_status(jm, status):
    snap = MagicMock(exists=True)
    snap.to_dict.return_value = {"status": status}
    jm._doc_ref.get.return_value = snap


@pytest.fixture
def prepare():
    fn = MagicMock(return_value=("jobs/x/style/style_params.json", {"intro_background": "themes/t/bg.jpg"}, None))
    with patch("backend.api.routes.file_upload._prepare_theme_for_job", fn), \
         patch("backend.services.theme_rerender_service.firestore.transactional", _passthrough_transactional):
        yield fn


class TestRefreshInflightJobs:
    def _run(self, jobs, prepare, claim_status_for=None):
        jm = _job_manager()
        jm.list_jobs.return_value = jobs
        # The transactional claim re-reads the job's status: return the job's own.
        statuses = iter([claim_status_for or j.status.value for j in jobs
                         if j.status in svc.REFRESHABLE_STATUSES and j.theme_id == "randy-vild"])

        def get(transaction=None):
            snap = MagicMock(exists=True)
            snap.to_dict.return_value = {"status": next(statuses)}
            return snap
        jm._doc_ref.get.side_effect = get
        result = svc.refresh_inflight_jobs("randy-vild", "randy-vild", job_manager=jm)
        updates = [c.args[1] for c in jm.firestore.db.transaction.return_value.update.call_args_list]
        return result, updates

    def test_review_stage_job_resnapshotted_and_flagged(self, prepare):
        result, updates = self._run([_job(status=JobStatus.IN_REVIEW)], prepare)
        assert result == {"updated": 1, "failed": 0}
        update = updates[0]
        assert update["style_params_gcs_path"] == "jobs/x/style/style_params.json"
        assert update["style_assets"] == {"intro_background": "themes/t/bg.jpg"}
        assert update["state_data.theme_screens_stale"] is True
        assert isinstance(update["theme_applied_at"], datetime)
        prepare.assert_called_once_with("job1", "randy-vild", None)

    def test_pre_screens_job_resnapshotted_without_flag(self, prepare):
        _, updates = self._run([_job(status=JobStatus.TRANSCRIBING)], prepare)
        assert "state_data.theme_screens_stale" not in updates[0]
        assert updates[0]["style_params_gcs_path"]

    def test_render_pending_capacity_is_flagged(self, prepare):
        """RVILD-0003 sat here while the theme was edited — it must pick up the change."""
        _, updates = self._run([_job(status=JobStatus.RENDER_PENDING_CAPACITY)], prepare)
        assert updates[0]["state_data.theme_screens_stale"] is True

    @pytest.mark.parametrize("status", [
        JobStatus.REVIEW_COMPLETE, JobStatus.RENDERING_VIDEO, JobStatus.ENCODING,
        JobStatus.COMPLETE, JobStatus.FAILED, JobStatus.CANCELLED,
    ])
    def test_rendering_and_terminal_jobs_untouched(self, prepare, status):
        result, updates = self._run([_job(status=status)], prepare)
        assert result == {"updated": 0, "failed": 0}
        assert updates == []
        prepare.assert_not_called()

    def test_job_on_another_theme_untouched(self, prepare):
        result, _ = self._run([_job(theme_id="nomad")], prepare)
        assert result["updated"] == 0
        prepare.assert_not_called()

    def test_job_that_moved_on_is_skipped(self, prepare):
        result, updates = self._run([_job(status=JobStatus.IN_REVIEW)], prepare, claim_status_for="rendering_video")
        assert result == {"updated": 0, "failed": 0}
        assert updates == []

    def test_one_failure_does_not_block_others(self, prepare):
        prepare.side_effect = [RuntimeError("gcs down"), ("p", {}, None)]
        result, updates = self._run([_job("a"), _job("b")], prepare)
        assert result == {"updated": 1, "failed": 1}
        assert len(updates) == 1


class TestDivertForStaleScreens:
    def _jm(self, status="review_complete"):
        jm = _job_manager()
        _set_claim_status(jm, status)
        return jm

    @pytest.mark.asyncio
    async def test_no_flag_renders_normally(self):
        jm = self._jm()
        job = _job(status=JobStatus.REVIEW_COMPLETE)
        assert await svc.divert_for_stale_screens(job, jm, MagicMock()) is False
        jm.firestore.db.transaction.assert_not_called()

    @pytest.mark.asyncio
    async def test_flag_ignored_outside_review_complete(self):
        job = _job(status=JobStatus.IN_REVIEW, state_data={"theme_screens_stale": True})
        assert await svc.divert_for_stale_screens(job, self._jm(), MagicMock()) is False

    @pytest.mark.asyncio
    async def test_stale_screens_regenerated_before_render(self):
        jm = self._jm()
        storage = MagicMock()
        storage.list_files.return_value = ["jobs/job1/finals/A - B (Title).mov", "jobs/job1/finals/x.mp4"]
        worker_service = MagicMock(trigger_screens_worker=AsyncMock(return_value=True))
        job = _job(status=JobStatus.REVIEW_COMPLETE, state_data={"theme_screens_stale": True})

        with patch("backend.services.theme_rerender_service.firestore.transactional", _passthrough_transactional), \
             patch("backend.services.worker_service.get_worker_service", return_value=worker_service):
            assert await svc.divert_for_stale_screens(job, jm, storage) is True

        update = jm.firestore.db.transaction.return_value.update.call_args.args[1]
        assert update["status"] == "lyrics_complete"
        assert update["state_data.regen_restore_status"] == "review_complete"
        assert update["state_data.theme_screens_stale"] is DELETE_FIELD
        assert update["file_urls.screens"] is DELETE_FIELD
        deleted = {c.args[0] for c in storage.delete_file.call_args_list}
        assert "jobs/job1/screens/title.png" in deleted
        assert "jobs/job1/finals/A - B (Title).mov" in deleted
        assert "jobs/job1/finals/x.mp4" not in deleted
        worker_service.trigger_screens_worker.assert_awaited_once_with("job1")

    @pytest.mark.asyncio
    async def test_already_diverted_by_duplicate_trigger_skips_render(self):
        jm = self._jm(status="generating_screens")
        job = _job(status=JobStatus.REVIEW_COMPLETE, state_data={"theme_screens_stale": True})
        with patch("backend.services.theme_rerender_service.firestore.transactional", _passthrough_transactional):
            assert await svc.divert_for_stale_screens(job, jm, MagicMock()) is True
        jm.firestore.db.transaction.return_value.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_trigger_failure_restores_and_raises(self):
        jm = self._jm()
        worker_service = MagicMock(trigger_screens_worker=AsyncMock(return_value=False))
        job = _job(status=JobStatus.REVIEW_COMPLETE, state_data={"theme_screens_stale": True})
        with patch("backend.services.theme_rerender_service.firestore.transactional", _passthrough_transactional), \
             patch("backend.services.worker_service.get_worker_service", return_value=worker_service):
            with pytest.raises(RuntimeError):
                await svc.divert_for_stale_screens(job, jm, MagicMock())
        restore = jm.update_job.call_args.args[1]
        assert restore["status"] == "review_complete"
        assert restore["state_data.theme_screens_stale"] is True  # a retry diverts again


class TestOutdatedJobs:
    def _run(self, jobs, updated_at=THEME_SAVED, user_email=None):
        jm = MagicMock()
        jm.list_jobs.return_value = jobs
        with patch.object(svc, "theme_updated_at", return_value=updated_at), \
             patch("backend.services.theme_rerender_service.validate_rerender",
                   side_effect=lambda j: "nope" if j.job_id == "invalid" else None):
            result = svc.outdated_jobs("randy-vild", "randy-vild", user_email, job_manager=jm)
        return result, jm

    def test_lists_finished_tracks_made_before_the_theme_save(self):
        jobs = [
            _job("old", status=JobStatus.COMPLETE),
            _job("rerendered", status=JobStatus.COMPLETE, theme_applied_at=THEME_SAVED + timedelta(minutes=5)),
            _job("new", status=JobStatus.COMPLETE, created_at=THEME_SAVED + timedelta(hours=1)),
            _job("inreview", status=JobStatus.IN_REVIEW),
            _job("othertheme", status=JobStatus.COMPLETE, theme_id="nomad"),
            _job("invalid", status=JobStatus.COMPLETE),
        ]
        result, _ = self._run(jobs)
        assert result["job_ids"] == ["old"]
        assert result["theme_updated_at"] == THEME_SAVED.isoformat()

    def test_scoped_to_owner(self):
        _, jm = self._run([], user_email="randy@example.com")
        assert jm.list_jobs.call_args.kwargs["user_email"] == "randy@example.com"
        assert jm.list_jobs.call_args.kwargs["tenant_id"] == "randy-vild"

    def test_unknown_theme_time_means_nothing_outdated(self):
        result, jm = self._run([_job(status=JobStatus.COMPLETE)], updated_at=None)
        assert result["job_ids"] == []
        jm.list_jobs.assert_not_called()

    def test_string_and_naive_timestamps(self):
        assert svc._as_utc("2026-10-06T14:35:00Z") == THEME_SAVED
        assert svc._as_utc(datetime(2026, 10, 6, 14, 35)) == THEME_SAVED
        assert svc._as_utc("garbage") is None


class TestThemeUpdatedAt:
    def test_reads_style_blob_time(self):
        storage = MagicMock()
        storage.bucket.get_blob.return_value = SimpleNamespace(updated=THEME_SAVED)
        assert svc.theme_updated_at("randy-vild", storage) == THEME_SAVED
        storage.bucket.get_blob.assert_called_once_with("themes/randy-vild/style_params.json")

    def test_missing_blob_or_error(self):
        storage = MagicMock()
        storage.bucket.get_blob.return_value = None
        assert svc.theme_updated_at("t", storage) is None
        storage.bucket.get_blob.side_effect = RuntimeError("boom")
        assert svc.theme_updated_at("t", storage) is None


class TestEditThemeUpdate:
    def _run(self, job, updated_at=THEME_SAVED, screens_regenerating=False, tenant_theme="randy-vild"):
        config = SimpleNamespace(defaults=SimpleNamespace(locked_theme=tenant_theme, theme_id=tenant_theme), id="randy-vild")
        with patch("backend.services.tenant_service.get_tenant_service") as gts, \
             patch.object(svc, "theme_updated_at", return_value=updated_at):
            gts.return_value.get_tenant_config.return_value = config
            return svc.edit_theme_update(job, screens_regenerating=screens_regenerating)

    def test_outdated_track_gets_current_theme_and_stale_flag(self, prepare):
        update = self._run(_job(status=JobStatus.COMPLETE))
        assert update["style_params_gcs_path"] == "jobs/x/style/style_params.json"
        assert update["state_data.theme_screens_stale"] is True

    def test_no_flag_when_edit_already_regenerates_screens(self, prepare):
        update = self._run(_job(status=JobStatus.COMPLETE), screens_regenerating=True)
        assert update["style_params_gcs_path"]
        assert "state_data.theme_screens_stale" not in update

    def test_current_track_unchanged(self, prepare):
        job = _job(status=JobStatus.COMPLETE, theme_applied_at=THEME_SAVED + timedelta(minutes=1))
        assert self._run(job) == {}
        prepare.assert_not_called()

    def test_non_tenant_and_theme_switched_jobs_unchanged(self, prepare):
        assert self._run(_job(tenant_id="")) == {}
        assert self._run(_job(), tenant_theme="other") == {}
        prepare.assert_not_called()


class TestQuietBulkRerender:
    def test_quiet_theme_rerender_suppresses_notifications(self):
        from backend.services.admin_rerender_service import suppress_customer_notifications
        quiet = SimpleNamespace(state_data={"theme_rerender": {"theme_id": "t", "notify_customer": False}})
        loud = SimpleNamespace(state_data={"theme_rerender": {"theme_id": "t", "notify_customer": True}})
        legacy = SimpleNamespace(state_data={"theme_rerender": {"theme_id": "t"}})
        assert suppress_customer_notifications(quiet) is True
        assert suppress_customer_notifications(loud) is False
        assert suppress_customer_notifications(legacy) is False

    @pytest.mark.asyncio
    async def test_marker_records_notify_choice_and_clears_stale_flag(self, prepare):
        from backend.services.theme_rerender_service import ThemeRerenderService
        jm = _job_manager()
        _set_claim_status(jm, "complete")
        worker_service = MagicMock(trigger_screens_worker=AsyncMock(return_value=True))
        job = SimpleNamespace(
            job_id="job1", status="complete", tenant_id="randy-vild", theme_id="randy-vild",
            color_overrides={}, outputs_deleted_at=None, prep_only=False, finalise_only=False,
            state_data={"instrumental_selection": "custom"},
            file_urls={"lyrics": {"corrections": "c.json"}}, progress=100,
        )
        with patch("backend.services.worker_service.get_worker_service", return_value=worker_service):
            await ThemeRerenderService(job_manager=jm, storage=MagicMock()).start(
                job, theme_id="randy-vild", requested_by="x", notify_customer=False)
        update = jm.firestore.db.transaction.return_value.update.call_args.args[1]
        assert update["state_data.theme_rerender"]["notify_customer"] is False
        assert update["state_data.theme_screens_stale"] is DELETE_FIELD
        assert isinstance(update["theme_applied_at"], datetime)


# --- Routes ------------------------------------------------------------------

from backend.api.routes import tenant_theme  # noqa: E402
from backend.tests.test_tenant_theme_route import RANDY, THEME, app, client_for  # noqa: E402,F401


def test_save_reports_refreshed_and_outdated(client_for):
    client = client_for()
    with patch.object(tenant_theme, "save_tenant_theme"), \
         patch.object(tenant_theme, "get_theme_for_editor", return_value=THEME), \
         patch.object(tenant_theme, "refresh_inflight_jobs", return_value={"updated": 3, "failed": 0}) as refresh, \
         patch.object(tenant_theme, "outdated_jobs", return_value={"job_ids": ["a", "b"]}) as outdated:
        resp = client.put("/api/tenant/theme", json={"style_params": {}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["refreshed_jobs"] == 3 and body["outdated_job_ids"] == ["a", "b"]
    assert body["theme_id"] == "randy-vild"
    refresh.assert_called_once_with("randy-vild", "randy-vild")
    assert outdated.call_args.args == ("randy-vild", "randy-vild", "randyvild@gmail.com")


def test_save_succeeds_even_if_follow_up_work_fails(client_for):
    client = client_for()
    with patch.object(tenant_theme, "save_tenant_theme"), \
         patch.object(tenant_theme, "get_theme_for_editor", return_value=THEME), \
         patch.object(tenant_theme, "refresh_inflight_jobs", side_effect=RuntimeError("x")), \
         patch.object(tenant_theme, "outdated_jobs", side_effect=RuntimeError("y")):
        resp = client.put("/api/tenant/theme", json={"style_params": {}})
    assert resp.status_code == 200
    assert resp.json()["refreshed_jobs"] == 0 and resp.json()["outdated_job_ids"] == []


def test_outdated_jobs_scope_member_vs_admin(client_for):
    with patch.object(tenant_theme, "outdated_jobs", return_value={"theme_updated_at": None, "job_ids": ["a"]}) as od:
        assert client_for().get("/api/tenant/theme/outdated-jobs").json()["job_ids"] == ["a"]
        assert od.call_args.args[2] == "randyvild@gmail.com"
        client_for(email="andrew@nomadkaraoke.com").get("/api/tenant/theme/outdated-jobs")
        assert od.call_args.args[2] is None


def test_rerender_outdated_is_quiet_and_reports_failures(client_for):
    from backend.services.theme_rerender_service import RerenderError
    service = MagicMock()
    service.start = AsyncMock(side_effect=[None, RerenderError("published"), None])
    jm = MagicMock()
    jm.get_job.side_effect = lambda jid: None if jid == "gone" else SimpleNamespace(job_id=jid)
    with patch.object(tenant_theme, "outdated_jobs", return_value={"job_ids": ["a", "b", "gone", "c"]}), \
         patch("backend.services.job_manager.JobManager", return_value=jm), \
         patch("backend.services.theme_rerender_service.ThemeRerenderService", return_value=service):
        resp = client_for().post("/api/tenant/theme/rerender-outdated")
    assert resp.status_code == 200
    assert resp.json() == {"started": ["a", "c"], "failed": {"b": "published"}, "remaining": []}
    assert all(c.kwargs["notify_customer"] is False for c in service.start.call_args_list)


def test_new_endpoints_reject_non_members(client_for):
    client = client_for(email="stranger@example.com")
    assert client.get("/api/tenant/theme/outdated-jobs").status_code == 403
    assert client.post("/api/tenant/theme/rerender-outdated").status_code == 403


def test_rerender_outdated_caps_each_call(client_for):
    """Bounded per request; the rest come back as ``remaining`` for the UI to offer again."""
    service = MagicMock()
    service.start = AsyncMock(return_value=None)
    jm = MagicMock()
    jm.get_job.side_effect = lambda jid: SimpleNamespace(job_id=jid)
    ids = [f"j{i}" for i in range(tenant_theme.MAX_BULK_RERENDER + 3)]
    with patch.object(tenant_theme, "outdated_jobs", return_value={"job_ids": ids}), \
         patch("backend.services.job_manager.JobManager", return_value=jm), \
         patch("backend.services.theme_rerender_service.ThemeRerenderService", return_value=service):
        body = client_for().post("/api/tenant/theme/rerender-outdated").json()
    assert body["started"] == ids[:tenant_theme.MAX_BULK_RERENDER]
    assert body["remaining"] == ids[tenant_theme.MAX_BULK_RERENDER:]
    assert service.start.await_count == tenant_theme.MAX_BULK_RERENDER
