"""
Unit tests for internal.py API routes using FastAPI TestClient.

Internal routes are used for worker communication within Cloud Run.
These endpoints require admin authentication.

With Cloud Tasks integration, these endpoints include idempotency checks
to prevent duplicate processing on retries.
"""
import os
import pytest
from datetime import datetime, UTC
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi.testclient import TestClient

from backend.models.job import Job, JobStatus


# Set test admin token for auth
os.environ.setdefault('ADMIN_TOKENS', 'test-admin-token')


def _create_mock_job(job_id: str = "test123") -> Job:
    """Create a mock Job object for testing."""
    return Job(
        job_id=job_id,
        status=JobStatus.PROCESSING,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        state_data={},  # Empty state_data - no worker progress yet
    )


@pytest.fixture
def client():
    """Create TestClient with mocked workers and job manager."""
    mock_creds = MagicMock()
    mock_creds.universe_domain = 'googleapis.com'
    
    # Mock the worker functions at the internal routes module level
    mock_audio = AsyncMock()
    mock_lyrics = AsyncMock()
    mock_screens = AsyncMock()
    mock_video = AsyncMock()
    mock_render = AsyncMock()
    
    # Mock JobManager for idempotency checks
    mock_job_manager = MagicMock()
    mock_job_manager.get_job.return_value = _create_mock_job()
    mock_job_manager.update_state_data.return_value = None
    
    with patch('backend.api.routes.internal.process_audio_separation', mock_audio), \
         patch('backend.api.routes.internal.process_lyrics_transcription', mock_lyrics), \
         patch('backend.api.routes.internal.generate_screens', mock_screens), \
         patch('backend.api.routes.internal.generate_video', mock_video), \
         patch('backend.api.routes.internal.process_render_video', mock_render), \
         patch('backend.api.routes.internal.JobManager', return_value=mock_job_manager), \
         patch('backend.services.firestore_service.firestore'), \
         patch('backend.services.storage_service.storage'), \
         patch('google.auth.default', return_value=(mock_creds, 'test-project')):
        from backend.main import app
        yield TestClient(app)


@pytest.fixture
def auth_headers():
    """Auth headers for internal API testing."""
    return {"Authorization": "Bearer test-admin-token"}


class TestAudioWorkerEndpoint:
    """Tests for POST /api/internal/workers/audio."""
    
    def test_audio_worker_returns_200(self, client, auth_headers):
        """Test audio worker endpoint returns 200 with auth."""
        response = client.post(
            "/api/internal/workers/audio",
            headers=auth_headers,
            json={"job_id": "test123"}
        )
        assert response.status_code == 200
    
    def test_audio_worker_accepts_job_id(self, client, auth_headers):
        """Test audio worker accepts job_id in body."""
        response = client.post(
            "/api/internal/workers/audio",
            headers=auth_headers,
            json={"job_id": "any-job-id"}
        )
        assert response.status_code == 200
    
    def test_audio_worker_requires_job_id(self, client, auth_headers):
        """Test audio worker requires job_id."""
        response = client.post(
            "/api/internal/workers/audio",
            headers=auth_headers,
            json={}
        )
        assert response.status_code == 422
    
    def test_audio_worker_requires_auth(self, client, auth_headers):
        """Test audio worker requires authentication."""
        response = client.post(
            "/api/internal/workers/audio",
            json={"job_id": "test123"}
        )
        assert response.status_code == 401


class TestLyricsWorkerEndpoint:
    """Tests for POST /api/internal/workers/lyrics."""
    
    def test_lyrics_worker_returns_200(self, client, auth_headers):
        """Test lyrics worker endpoint returns 200 with auth."""
        response = client.post(
            "/api/internal/workers/lyrics",
            headers=auth_headers,
            json={"job_id": "test123"}
        )
        assert response.status_code == 200
    
    def test_lyrics_worker_requires_job_id(self, client, auth_headers):
        """Test lyrics worker requires job_id."""
        response = client.post(
            "/api/internal/workers/lyrics",
            headers=auth_headers,
            json={}
        )
        assert response.status_code == 422
    
    def test_lyrics_worker_requires_auth(self, client, auth_headers):
        """Test lyrics worker requires authentication."""
        response = client.post(
            "/api/internal/workers/lyrics",
            json={"job_id": "test123"}
        )
        assert response.status_code == 401


class TestScreensWorkerEndpoint:
    """Tests for POST /api/internal/workers/screens."""
    
    def test_screens_worker_returns_200(self, client, auth_headers):
        """Test screens worker endpoint returns 200 with auth."""
        response = client.post(
            "/api/internal/workers/screens",
            headers=auth_headers,
            json={"job_id": "test123"}
        )
        assert response.status_code == 200
    
    def test_screens_worker_requires_job_id(self, client, auth_headers):
        """Test screens worker requires job_id."""
        response = client.post(
            "/api/internal/workers/screens",
            headers=auth_headers,
            json={}
        )
        assert response.status_code == 422
    
    def test_screens_worker_requires_auth(self, client, auth_headers):
        """Test screens worker requires authentication."""
        response = client.post(
            "/api/internal/workers/screens",
            json={"job_id": "test123"}
        )
        assert response.status_code == 401


class TestVideoWorkerEndpoint:
    """Tests for POST /api/internal/workers/video."""
    
    def test_video_worker_returns_200(self, client, auth_headers):
        """Test video worker endpoint returns 200 with auth."""
        response = client.post(
            "/api/internal/workers/video",
            headers=auth_headers,
            json={"job_id": "test123"}
        )
        assert response.status_code == 200
    
    def test_video_worker_requires_job_id(self, client, auth_headers):
        """Test video worker requires job_id."""
        response = client.post(
            "/api/internal/workers/video",
            headers=auth_headers,
            json={}
        )
        assert response.status_code == 422
    
    def test_video_worker_requires_auth(self, client, auth_headers):
        """Test video worker requires authentication."""
        response = client.post(
            "/api/internal/workers/video",
            json={"job_id": "test123"}
        )
        assert response.status_code == 401


class TestRenderVideoWorkerEndpoint:
    """Tests for POST /api/internal/workers/render-video."""
    
    def test_render_video_worker_returns_200(self, client, auth_headers):
        """Test render-video worker endpoint returns 200 with auth."""
        response = client.post(
            "/api/internal/workers/render-video",
            headers=auth_headers,
            json={"job_id": "test123"}
        )
        assert response.status_code == 200
    
    def test_render_video_worker_requires_job_id(self, client, auth_headers):
        """Test render-video worker requires job_id."""
        response = client.post(
            "/api/internal/workers/render-video",
            headers=auth_headers,
            json={}
        )
        assert response.status_code == 422
    
    def test_render_video_worker_requires_auth(self, client, auth_headers):
        """Test render-video worker requires authentication."""
        response = client.post(
            "/api/internal/workers/render-video",
            json={"job_id": "test123"}
        )
        assert response.status_code == 401


class TestWorkerResponseFormat:
    """Tests for worker response format."""
    
    def test_audio_worker_response_contains_status(self, client, auth_headers):
        """Test audio worker response contains status."""
        response = client.post(
            "/api/internal/workers/audio",
            headers=auth_headers,
            json={"job_id": "test123"}
        )
        data = response.json()
        assert "status" in data or "message" in data
    
    def test_lyrics_worker_response_contains_status(self, client, auth_headers):
        """Test lyrics worker response contains status."""
        response = client.post(
            "/api/internal/workers/lyrics",
            headers=auth_headers,
            json={"job_id": "test123"}
        )
        data = response.json()
        assert "status" in data or "message" in data


class TestWorkerIdempotency:
    """Tests for worker idempotency checks (Cloud Tasks retry support)."""
    
    def test_audio_worker_returns_already_running_when_in_progress(self, auth_headers):
        """Test audio worker returns already_running if worker is already running."""
        mock_creds = MagicMock()
        mock_creds.universe_domain = 'googleapis.com'
        
        # Create job with audio already running
        mock_job = _create_mock_job()
        mock_job.state_data = {'audio_progress': {'stage': 'running'}}
        
        mock_job_manager = MagicMock()
        mock_job_manager.get_job.return_value = mock_job
        
        with patch('backend.api.routes.internal.process_audio_separation', AsyncMock()), \
             patch('backend.api.routes.internal.JobManager', return_value=mock_job_manager), \
             patch('backend.services.firestore_service.firestore'), \
             patch('backend.services.storage_service.storage'), \
             patch('google.auth.default', return_value=(mock_creds, 'test-project')):
            from backend.main import app
            client = TestClient(app)
            
            response = client.post(
                "/api/internal/workers/audio",
                headers=auth_headers,
                json={"job_id": "test123"}
            )
            
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "already_running"
    
    def test_audio_worker_returns_already_complete_when_finished(self, auth_headers):
        """Test audio worker returns already_complete if worker already finished."""
        mock_creds = MagicMock()
        mock_creds.universe_domain = 'googleapis.com'
        
        # Create job with audio already complete
        mock_job = _create_mock_job()
        mock_job.state_data = {'audio_progress': {'stage': 'complete'}}
        
        mock_job_manager = MagicMock()
        mock_job_manager.get_job.return_value = mock_job
        
        with patch('backend.api.routes.internal.process_audio_separation', AsyncMock()), \
             patch('backend.api.routes.internal.JobManager', return_value=mock_job_manager), \
             patch('backend.services.firestore_service.firestore'), \
             patch('backend.services.storage_service.storage'), \
             patch('google.auth.default', return_value=(mock_creds, 'test-project')):
            from backend.main import app
            client = TestClient(app)
            
            response = client.post(
                "/api/internal/workers/audio",
                headers=auth_headers,
                json={"job_id": "test123"}
            )
            
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "already_complete"
    
    def test_audio_worker_returns_not_found_when_job_missing(self, auth_headers):
        """Test audio worker returns not_found if job doesn't exist."""
        mock_creds = MagicMock()
        mock_creds.universe_domain = 'googleapis.com'
        
        # Return None for job not found
        mock_job_manager = MagicMock()
        mock_job_manager.get_job.return_value = None
        
        with patch('backend.api.routes.internal.process_audio_separation', AsyncMock()), \
             patch('backend.api.routes.internal.JobManager', return_value=mock_job_manager), \
             patch('backend.services.firestore_service.firestore'), \
             patch('backend.services.storage_service.storage'), \
             patch('google.auth.default', return_value=(mock_creds, 'test-project')):
            from backend.main import app
            client = TestClient(app)
            
            response = client.post(
                "/api/internal/workers/audio",
                headers=auth_headers,
                json={"job_id": "nonexistent"}
            )
            
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "not_found"



class TestAutoApprovalEval:
    """POST /api/internal/jobs/{job_id}/auto-approval-eval — admin re-evaluation."""

    def test_eval_calls_executor_and_returns_outcome(self, client):
        with patch(
            'backend.services.auto_approval.executor.maybe_auto_complete_review',
            new=AsyncMock(return_value={"outcome": "auto_completed"}),
        ) as mock_eval:
            response = client.post(
                "/api/internal/jobs/test123/auto-approval-eval",
                headers={"Authorization": "Bearer test-admin-token"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["job_id"] == "test123"
            assert data["outcome"] == "auto_completed"
            mock_eval.assert_awaited_once_with("test123", trigger="admin_eval")

    def test_eval_requires_admin(self, client):
        response = client.post("/api/internal/jobs/test123/auto-approval-eval")
        assert response.status_code == 401
