"""Route-level tests for POST /api/jobs/{job_id}/corrections — timing sanitizer integration.

Verifies that out-of-bounds word timings submitted by the frontend are clamped
before corrections_updated.json is written to GCS.
"""
from __future__ import annotations

import pytest
from datetime import datetime, UTC
from unittest.mock import MagicMock, patch, call

from backend.models.job import Job, JobStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def review_job():
    """A job in AWAITING_REVIEW state owned by the test admin user."""
    job = Job(
        job_id="review-job-001",
        status=JobStatus.AWAITING_REVIEW,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        artist="Test Artist",
        title="Test Song",
    )
    return job


@pytest.fixture
def mock_job_manager(review_job):
    manager = MagicMock()
    manager.get_job.return_value = review_job
    manager.update_state_data.return_value = None
    manager.update_file_url.return_value = None
    manager.transition_to_state.return_value = None
    return manager


@pytest.fixture
def mock_worker_service():
    return MagicMock()


@pytest.fixture
def mock_theme_service():
    svc = MagicMock()
    svc.get_default_theme_id.return_value = "nomad"
    svc.get_theme.return_value = None
    return svc


@pytest.fixture
def client(mock_job_manager, mock_worker_service, mock_theme_service):
    """FastAPI TestClient with mocked dependencies."""
    from fastapi.testclient import TestClient

    mock_creds = MagicMock()
    mock_creds.universe_domain = "googleapis.com"

    def _jm_factory(*args, **kwargs):
        return mock_job_manager

    with patch("backend.api.routes.jobs.job_manager", mock_job_manager), \
         patch("backend.api.routes.jobs.worker_service", mock_worker_service), \
         patch("backend.api.routes.jobs.get_theme_service", return_value=mock_theme_service), \
         patch("backend.services.job_manager.JobManager", _jm_factory), \
         patch("backend.services.firestore_service.firestore"), \
         patch("backend.services.storage_service.storage"), \
         patch("google.auth.default", return_value=(mock_creds, "test-project")):
        from backend.main import app
        yield TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bad_corrections():
    """Corrections payload whose words fall outside segment bounds (start=0, end<0).

    Must include the 'lines' and 'metadata' keys required by CorrectionsSubmission
    validation before the sanitizer runs.
    """
    return {
        "lines": [],
        "metadata": {"source": "test"},
        "corrected_segments": [
            {
                "id": "s1",
                "text": "A whiskey and a beer,",
                "start_time": 15.18,
                "end_time": 18.04,
                "words": [
                    {"id": "a", "text": "A",       "start_time": 0,    "end_time": -0.005},
                    {"id": "b", "text": "whiskey",  "start_time": 0,    "end_time": -0.005},
                    {"id": "c", "text": "and",      "start_time": 0,    "end_time": 0},
                    {"id": "d", "text": "a",        "start_time": 0,    "end_time": 0},
                    {"id": "e", "text": "beer,",    "start_time": 16.1, "end_time": 16.42},
                ],
            }
        ]
    }


def _good_corrections():
    """Corrections payload whose word timings are already valid."""
    return {
        "lines": [],
        "metadata": {"source": "test"},
        "corrected_segments": [
            {
                "id": "s1",
                "text": "Filling up",
                "start_time": 21.4,
                "end_time": 22.1,
                "words": [
                    {"id": "w0", "text": "Filling", "start_time": 21.4,  "end_time": 21.82},
                    {"id": "w1", "text": "up",      "start_time": 21.86, "end_time": 22.1},
                ],
            }
        ]
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSubmitCorrectionsTimingSanitizer:
    """Verify that submit_corrections clamps out-of-bounds word timings before GCS upload."""

    def test_sanitized_timings_uploaded_to_gcs(self, client, auth_headers):
        """Words with start_time/end_time outside segment bounds must be clamped
        before corrections_updated.json is written to GCS.

        Patch StorageService at the class level in its source module so the local
        import inside submit_corrections picks up the mock.
        """
        uploaded_payload = {}

        with patch("backend.services.storage_service.StorageService") as MockStorage:
            storage_instance = MockStorage.return_value
            # Capture what gets passed to upload_json
            def capture_upload(path, data):
                uploaded_payload.update(data)
            storage_instance.upload_json.side_effect = capture_upload

            response = client.post(
                "/api/jobs/review-job-001/corrections",
                json={"corrections": _bad_corrections()},
                headers=auth_headers,
            )

        assert response.status_code == 200, response.text
        assert uploaded_payload, "upload_json was never called — capture failed"

        # All uploaded word timings must be within their segment bounds
        words = uploaded_payload["corrected_segments"][0]["words"]
        seg_start = 15.18
        seg_end = 18.04
        for w in words:
            assert w["start_time"] >= seg_start, f"word start_time {w['start_time']} < seg start {seg_start}"
            assert w["end_time"] >= w["start_time"], f"word end_time {w['end_time']} < start_time {w['start_time']}"
            assert w["end_time"] <= seg_end + 1e-9, f"word end_time {w['end_time']} > seg end {seg_end}"

    def test_valid_timings_uploaded_unchanged(self, client, auth_headers):
        """Word timings already within bounds must not be modified."""
        uploaded_payload = {}

        with patch("backend.services.storage_service.StorageService") as MockStorage:
            storage_instance = MockStorage.return_value
            def capture_upload(path, data):
                uploaded_payload.update(data)
            storage_instance.upload_json.side_effect = capture_upload

            response = client.post(
                "/api/jobs/review-job-001/corrections",
                json={"corrections": _good_corrections()},
                headers=auth_headers,
            )

        assert response.status_code == 200, response.text
        assert uploaded_payload, "upload_json was never called — capture failed"

        words = uploaded_payload["corrected_segments"][0]["words"]
        assert words[0]["start_time"] == 21.4
        assert words[0]["end_time"] == 21.82
        assert words[1]["start_time"] == 21.86
        assert words[1]["end_time"] == 22.1


class TestSubmitCorrectionsContract:
    """Minimal contract test — does not require the full FastAPI test harness."""

    def test_sanitize_contract_bad_timings(self):
        """sanitize_corrections clamps words with start/end outside segment bounds."""
        from backend.services.timing_sanitizer import sanitize_corrections

        bad = {
            "corrected_segments": [
                {
                    "id": "s", "start_time": 15.18, "end_time": 18.04, "text": "A",
                    "words": [{"id": "a", "text": "A", "start_time": 0, "end_time": -0.005}],
                }
            ]
        }
        cleaned, count = sanitize_corrections(bad)
        assert count >= 1
        w = cleaned["corrected_segments"][0]["words"][0]
        assert w["start_time"] >= 15.18
        assert w["end_time"] >= w["start_time"]
