"""HTTP tests for the stalled-download recovery endpoints.

- POST /api/jobs/{id}/retry accepts an optional {"keep_trying": true} body that
  retries an audio-search download with the extended (1 hour) stall budget.
- POST /api/jobs/{id}/choose-different-audio reopens audio selection for a job
  whose audio download failed.
"""
from datetime import datetime, UTC, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.job import Job, JobStatus
from backend.services.audio_download_limits import KEEP_TRYING_STATE_KEY

JOB_ID = "stalljob1"


def _stalled_job(**overrides) -> Job:
    fields = dict(
        job_id=JOB_ID,
        status=JobStatus.FAILED,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        artist="Test Artist",
        title="Test Song",
        user_email="owner@example.com",
        audio_source_type="audio_search",
        source_name="RED",
        source_id="987654",
        input_media_gcs_path=None,
        file_urls={},
        error_message="Audio download didn't start within 20 minutes",
        error_details={
            "stage": "audio_download",
            "code": "audio_download_stalled",
            "stall_minutes": 20,
            "keep_trying": False,
        },
        state_data={
            "audio_search_results": [
                {"provider": "RED", "source_id": "987654", "title": "Test Song"},
                {"provider": "Spotify", "source_id": "sp1", "title": "Test Song"},
            ],
            "selected_audio_index": 0,
        },
    )
    fields.update(overrides)
    return Job(**fields)


@pytest.fixture
def mock_jm():
    jm = MagicMock()
    jm.get_job.return_value = _stalled_job()
    jm.transition_to_state.return_value = True
    return jm


@pytest.fixture
def mock_ws():
    ws = MagicMock()
    ws.trigger_audio_download_worker = AsyncMock(return_value=True)
    return ws


@pytest.fixture
def client(mock_jm, mock_ws):
    mock_creds = MagicMock()
    mock_creds.universe_domain = "googleapis.com"
    with patch("backend.api.routes.jobs.job_manager", mock_jm), \
         patch("backend.api.routes.jobs.worker_service", mock_ws), \
         patch("backend.api.routes.jobs.get_locale_from_request", return_value="en"), \
         patch("backend.services.firestore_service.firestore"), \
         patch("backend.services.storage_service.storage"), \
         patch("google.auth.default", return_value=(mock_creds, "test-project")):
        from backend.main import app
        from fastapi.testclient import TestClient
        yield TestClient(app)


def _keep_trying_updates(mock_jm):
    return [c.args[2] for c in mock_jm.update_state_data.call_args_list
            if c.args[0] == JOB_ID and c.args[1] == KEEP_TRYING_STATE_KEY]


class TestRetryKeepTrying:

    def test_keep_trying_body_sets_flag_and_extended_trigger(self, client, mock_jm, mock_ws, auth_headers):
        resp = client.post(f"/api/jobs/{JOB_ID}/retry", headers=auth_headers, json={"keep_trying": True})

        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["retry_stage"] == "audio_download"
        assert data["keep_trying"] is True
        assert _keep_trying_updates(mock_jm) == [True]
        mock_ws.trigger_audio_download_worker.assert_awaited_once_with(JOB_ID, keep_trying=True)
        assert mock_jm.transition_to_state.call_args.kwargs["new_status"] == JobStatus.DOWNLOADING_AUDIO

    def test_no_body_defaults_to_normal_budget(self, client, mock_jm, mock_ws):
        """Existing callers POST with no body at all — must keep working."""
        resp = client.post(f"/api/jobs/{JOB_ID}/retry", headers={"Authorization": "Bearer test-admin-token"})

        assert resp.status_code == 200, resp.text
        assert resp.json()["keep_trying"] is False
        assert _keep_trying_updates(mock_jm) == [None]
        mock_ws.trigger_audio_download_worker.assert_awaited_once_with(JOB_ID, keep_trying=False)

    def test_empty_body_with_json_content_type(self, client, mock_jm, mock_ws, auth_headers):
        """Callers that send Content-Type: application/json but no body still work."""
        resp = client.post(f"/api/jobs/{JOB_ID}/retry", headers=auth_headers)

        assert resp.status_code == 200, resp.text
        mock_ws.trigger_audio_download_worker.assert_awaited_once_with(JOB_ID, keep_trying=False)

    def test_explicit_false_body(self, client, mock_jm, mock_ws, auth_headers):
        resp = client.post(f"/api/jobs/{JOB_ID}/retry", headers=auth_headers, json={"keep_trying": False})

        assert resp.status_code == 200, resp.text
        assert _keep_trying_updates(mock_jm) == [None]
        mock_ws.trigger_audio_download_worker.assert_awaited_once_with(JOB_ID, keep_trying=False)

    def test_retry_pending_blocks_keep_trying(self, client, mock_jm, mock_ws, auth_headers):
        expires = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
        mock_jm.get_job.return_value = _stalled_job(
            state_data={"cloud_run_retry_pending": {"expires_at": expires, "expected_attempt": 1}},
        )

        resp = client.post(f"/api/jobs/{JOB_ID}/retry", headers=auth_headers, json={"keep_trying": True})

        assert resp.status_code == 409
        mock_ws.trigger_audio_download_worker.assert_not_awaited()


class TestChooseDifferentAudio:

    def test_reopens_audio_selection(self, client, mock_jm, auth_headers):
        resp = client.post(f"/api/jobs/{JOB_ID}/choose-different-audio", headers=auth_headers)

        assert resp.status_code == 200, resp.text
        assert resp.json()["job_status"] == "awaiting_audio_selection"
        mock_jm.transition_to_state.assert_called_once()
        assert mock_jm.transition_to_state.call_args.kwargs["new_status"] == JobStatus.AWAITING_AUDIO_SELECTION
        mock_jm.update_job.assert_any_call(JOB_ID, {"error_message": None, "error_details": None})
        assert _keep_trying_updates(mock_jm) == [None]

    @pytest.mark.parametrize("job_overrides", [
        pytest.param({"status": JobStatus.DOWNLOADING_AUDIO}, id="not-failed"),
        pytest.param({"error_details": {"stage": "audio_separation"}}, id="other-stage"),
        pytest.param({"error_details": None}, id="no-error-details"),
        pytest.param({"state_data": {}}, id="no-search-results"),
        pytest.param({"state_data": {"audio_search_results": []}}, id="empty-search-results"),
    ])
    def test_rejects_ineligible_jobs_with_400(self, client, mock_jm, auth_headers, job_overrides):
        mock_jm.get_job.return_value = _stalled_job(**job_overrides)

        resp = client.post(f"/api/jobs/{JOB_ID}/choose-different-audio", headers=auth_headers)

        assert resp.status_code == 400, resp.text
        mock_jm.transition_to_state.assert_not_called()

    def test_forbidden_for_non_owner(self, client, mock_jm, auth_headers):
        with patch("backend.api.routes.jobs._check_job_ownership", return_value=False):
            resp = client.post(f"/api/jobs/{JOB_ID}/choose-different-audio", headers=auth_headers)

        assert resp.status_code == 403
        mock_jm.transition_to_state.assert_not_called()

    def test_conflict_when_auto_retry_pending(self, client, mock_jm, auth_headers):
        expires = (datetime.now(UTC) + timedelta(minutes=10)).isoformat()
        job = _stalled_job()
        job.state_data["cloud_run_retry_pending"] = {"expires_at": expires, "expected_attempt": 1}
        mock_jm.get_job.return_value = job

        resp = client.post(f"/api/jobs/{JOB_ID}/choose-different-audio", headers=auth_headers)

        assert resp.status_code == 409
        mock_jm.transition_to_state.assert_not_called()

    def test_not_found(self, client, mock_jm, auth_headers):
        mock_jm.get_job.return_value = None

        resp = client.post(f"/api/jobs/{JOB_ID}/choose-different-audio", headers=auth_headers)

        assert resp.status_code == 404

    def test_transition_failure_returns_500(self, client, mock_jm, auth_headers):
        mock_jm.transition_to_state.return_value = False

        resp = client.post(f"/api/jobs/{JOB_ID}/choose-different-audio", headers=auth_headers)

        assert resp.status_code == 500
