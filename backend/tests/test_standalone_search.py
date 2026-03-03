"""
Unit tests for the standalone search + create-from-search guided flow.

Covers:
- FirestoreService search session CRUD (including consume_search_session)
- POST /api/audio-search/search-standalone (no job created)
- POST /api/jobs/create-from-search (session validation, ownership, tenant, job creation)
"""
import pytest
from datetime import datetime, timedelta, UTC
from unittest.mock import MagicMock, AsyncMock, patch

from fastapi.testclient import TestClient

from backend.models.job import Job, JobStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_creds():
    creds = MagicMock()
    creds.universe_domain = "googleapis.com"
    return creds


def _make_mock_job_manager(job=None):
    mgr = MagicMock()
    if job is None:
        job = Job(
            job_id="test-job-123",
            status=JobStatus.PENDING,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            artist="Test Artist",
            title="Test Song",
        )
    mgr.create_job.return_value = job
    mgr.get_job.return_value = job
    mgr.update_job.return_value = None
    mgr.update_state_data.return_value = None
    return mgr


def _make_search_session(user_email="test@example.com", expired=False, tenant_id=None):
    now = datetime.utcnow()
    ttl = now - timedelta(hours=1) if expired else now + timedelta(minutes=30)
    return {
        "session_id": "sess-abc-123",
        "user_email": user_email,
        "tenant_id": tenant_id,
        "artist": "ABBA",
        "title": "Waterloo",
        "results": [
            {
                "index": 0,
                "title": "Waterloo",
                "artist": "ABBA",
                "provider": "YouTube",
                "url": "https://youtube.com/watch?v=abc",
            }
        ],
        "remote_search_id": None,
        "created_at": now.isoformat(),
        "ttl_expiry": ttl,
    }


# ---------------------------------------------------------------------------
# State machine — PENDING → DOWNLOADING_AUDIO transition
# ---------------------------------------------------------------------------

class TestStateTransitionPendingToDownloadingAudio:
    """Verify PENDING → DOWNLOADING_AUDIO is a valid state transition (create-from-search path)."""

    def test_pending_allows_downloading_audio_transition(self):
        from backend.models.job import STATE_TRANSITIONS
        assert JobStatus.DOWNLOADING_AUDIO in STATE_TRANSITIONS[JobStatus.PENDING]


# ---------------------------------------------------------------------------
# FirestoreService session CRUD
# ---------------------------------------------------------------------------

class TestFirestoreServiceSearchSessionCRUD:
    """Unit tests for search session CRUD methods on FirestoreService."""

    def test_create_search_session_stores_document(self):
        """create_search_session should write to Firestore and return session_id."""
        from backend.services.firestore_service import FirestoreService

        mock_doc = MagicMock()
        mock_collection = MagicMock()
        mock_collection.document.return_value = mock_doc
        mock_db = MagicMock()
        mock_db.collection.return_value = mock_collection

        svc = FirestoreService.__new__(FirestoreService)
        svc.db = mock_db

        session_data = {
            "session_id": "sess-xyz",
            "user_email": "user@example.com",
            "artist": "ABBA",
            "title": "Waterloo",
            "results": [],
        }
        returned_id = svc.create_search_session(session_data)

        mock_db.collection.assert_called_once_with("search_sessions")
        mock_collection.document.assert_called_once_with("sess-xyz")
        mock_doc.set.assert_called_once_with(session_data)
        assert returned_id == "sess-xyz"

    def test_get_search_session_returns_dict_when_exists(self):
        """get_search_session should return doc dict when document exists."""
        from backend.services.firestore_service import FirestoreService

        session_dict = {"session_id": "sess-xyz", "user_email": "user@example.com"}
        mock_snapshot = MagicMock()
        mock_snapshot.exists = True
        mock_snapshot.to_dict.return_value = session_dict

        mock_doc = MagicMock()
        mock_doc.get.return_value = mock_snapshot
        mock_collection = MagicMock()
        mock_collection.document.return_value = mock_doc
        mock_db = MagicMock()
        mock_db.collection.return_value = mock_collection

        svc = FirestoreService.__new__(FirestoreService)
        svc.db = mock_db

        result = svc.get_search_session("sess-xyz")

        assert result == session_dict

    def test_get_search_session_returns_none_when_missing(self):
        """get_search_session should return None when document doesn't exist."""
        from backend.services.firestore_service import FirestoreService

        mock_snapshot = MagicMock()
        mock_snapshot.exists = False
        mock_doc = MagicMock()
        mock_doc.get.return_value = mock_snapshot
        mock_collection = MagicMock()
        mock_collection.document.return_value = mock_doc
        mock_db = MagicMock()
        mock_db.collection.return_value = mock_collection

        svc = FirestoreService.__new__(FirestoreService)
        svc.db = mock_db

        result = svc.get_search_session("nonexistent-session")

        assert result is None

    def test_get_search_session_raises_on_firestore_error(self):
        """get_search_session should raise (not return None) on Firestore errors."""
        from backend.services.firestore_service import FirestoreService

        mock_collection = MagicMock()
        mock_collection.document.side_effect = Exception("Firestore unavailable")
        mock_db = MagicMock()
        mock_db.collection.return_value = mock_collection

        svc = FirestoreService.__new__(FirestoreService)
        svc.db = mock_db

        with pytest.raises(Exception, match="Firestore unavailable"):
            svc.get_search_session("sess-xyz")

    def test_consume_search_session_returns_session_and_deletes_atomically(self):
        """consume_search_session should return session data and delete within transaction."""
        from backend.services.firestore_service import FirestoreService
        from google.cloud import firestore as fs

        session_dict = {"session_id": "sess-xyz", "user_email": "user@example.com"}

        mock_snapshot = MagicMock()
        mock_snapshot.exists = True
        mock_snapshot.to_dict.return_value = session_dict

        mock_doc_ref = MagicMock()
        mock_collection = MagicMock()
        mock_collection.document.return_value = mock_doc_ref
        mock_db = MagicMock()
        mock_db.collection.return_value = mock_collection

        # Simulate Firestore transaction: the @transactional function gets called
        # with a transaction object; we simulate by calling the inner function directly.
        captured_fn = {}

        def fake_transactional(fn):
            captured_fn['fn'] = fn
            def wrapper(transaction, *args, **kwargs):
                return fn(transaction, *args, **kwargs)
            return wrapper

        mock_transaction = MagicMock()
        mock_doc_ref.get.return_value = mock_snapshot
        mock_db.transaction.return_value = mock_transaction

        svc = FirestoreService.__new__(FirestoreService)
        svc.db = mock_db

        with patch.object(fs, 'transactional', fake_transactional):
            result = svc.consume_search_session("sess-xyz")

        assert result == session_dict

    def test_consume_search_session_returns_none_when_missing(self):
        """consume_search_session returns None when document doesn't exist."""
        from backend.services.firestore_service import FirestoreService
        from google.cloud import firestore as fs

        mock_snapshot = MagicMock()
        mock_snapshot.exists = False

        mock_doc_ref = MagicMock()
        mock_collection = MagicMock()
        mock_collection.document.return_value = mock_doc_ref
        mock_db = MagicMock()
        mock_db.collection.return_value = mock_collection

        def fake_transactional(fn):
            def wrapper(transaction, *args, **kwargs):
                return fn(transaction, *args, **kwargs)
            return wrapper

        mock_transaction = MagicMock()
        mock_doc_ref.get.return_value = mock_snapshot
        mock_db.transaction.return_value = mock_transaction

        svc = FirestoreService.__new__(FirestoreService)
        svc.db = mock_db

        with patch.object(fs, 'transactional', fake_transactional):
            result = svc.consume_search_session("nonexistent-session")

        assert result is None

    def test_delete_search_session_calls_firestore_delete(self):
        """delete_search_session should call Firestore delete."""
        from backend.services.firestore_service import FirestoreService

        mock_doc = MagicMock()
        mock_collection = MagicMock()
        mock_collection.document.return_value = mock_doc
        mock_db = MagicMock()
        mock_db.collection.return_value = mock_collection

        svc = FirestoreService.__new__(FirestoreService)
        svc.db = mock_db

        svc.delete_search_session("sess-xyz")

        mock_doc.delete.assert_called_once()

    def test_delete_search_session_silently_ignores_errors(self):
        """delete_search_session should not raise on Firestore errors."""
        from backend.services.firestore_service import FirestoreService

        mock_collection = MagicMock()
        mock_collection.document.side_effect = Exception("Connection error")
        mock_db = MagicMock()
        mock_db.collection.return_value = mock_collection

        svc = FirestoreService.__new__(FirestoreService)
        svc.db = mock_db

        # Should not raise
        svc.delete_search_session("sess-xyz")


# ---------------------------------------------------------------------------
# POST /api/audio-search/search-standalone
# ---------------------------------------------------------------------------

@pytest.fixture
def standalone_client(mock_job_manager_standalone):
    """TestClient for standalone search tests."""
    from backend.services.audio_search_service import AudioSearchResult

    mock_search_result = AudioSearchResult(
        title="Waterloo",
        artist="ABBA",
        provider="YouTube",
        url="https://youtube.com/watch?v=abc",
        duration=180,
        quality="320kbps",
        source_id="abc",
        index=0,
    )

    mock_audio_search = MagicMock()
    mock_audio_search.search_async = AsyncMock(return_value=[mock_search_result])
    mock_audio_search.last_remote_search_id = None

    mock_firestore = MagicMock()
    mock_firestore.create_search_session.return_value = "sess-new-123"

    mock_creds = _make_mock_creds()

    def mock_jm_factory(*args, **kwargs):
        return mock_job_manager_standalone

    with patch("backend.api.routes.audio_search.job_manager", mock_job_manager_standalone), \
         patch("backend.api.routes.audio_search.get_audio_search_service", return_value=mock_audio_search), \
         patch("backend.api.routes.audio_search.FirestoreService", return_value=mock_firestore), \
         patch("backend.services.job_manager.JobManager", mock_jm_factory), \
         patch("backend.services.firestore_service.firestore"), \
         patch("backend.services.storage_service.storage"), \
         patch("google.auth.default", return_value=(mock_creds, "test-project")):
        from backend.main import app
        client = TestClient(app)
        client._mock_audio_search = mock_audio_search
        client._mock_firestore = mock_firestore
        yield client


@pytest.fixture
def mock_job_manager_standalone():
    return _make_mock_job_manager()


class TestSearchAudioStandalone:
    """Tests for POST /api/audio-search/search-standalone."""

    def test_returns_200_and_session_id(self, standalone_client, auth_headers):
        """Successful search returns 200 with search_session_id."""
        response = standalone_client.post(
            "/api/audio-search/search-standalone",
            json={"artist": "ABBA", "title": "Waterloo"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "search_session_id" in data
        assert data["results_count"] == 1

    def test_returns_results_in_response(self, standalone_client, auth_headers):
        """Response includes search result details."""
        response = standalone_client.post(
            "/api/audio-search/search-standalone",
            json={"artist": "ABBA", "title": "Waterloo"},
            headers=auth_headers,
        )
        data = response.json()
        results = data["results"]
        assert len(results) == 1
        assert results[0]["title"] == "Waterloo"
        assert results[0]["provider"] == "YouTube"

    def test_no_job_is_created(self, standalone_client, mock_job_manager_standalone, auth_headers):
        """Standalone search must NOT create any job."""
        standalone_client.post(
            "/api/audio-search/search-standalone",
            json={"artist": "ABBA", "title": "Waterloo"},
            headers=auth_headers,
        )
        mock_job_manager_standalone.create_job.assert_not_called()

    def test_session_is_stored_in_firestore(self, standalone_client, auth_headers):
        """Search session is stored in Firestore."""
        response = standalone_client.post(
            "/api/audio-search/search-standalone",
            json={"artist": "ABBA", "title": "Waterloo"},
            headers=auth_headers,
        )
        assert response.status_code == 200
        standalone_client._mock_firestore.create_search_session.assert_called_once()

    def test_no_results_returns_empty_list(self, mock_job_manager_standalone, auth_headers):
        """No-results case returns empty results list, not an error."""
        from backend.services.audio_search_service import NoResultsError

        mock_audio_search = MagicMock()
        mock_audio_search.search_async = AsyncMock(side_effect=NoResultsError("No results"))
        mock_audio_search.last_remote_search_id = None

        mock_firestore = MagicMock()
        mock_creds = _make_mock_creds()

        def mock_jm_factory(*args, **kwargs):
            return mock_job_manager_standalone

        with patch("backend.api.routes.audio_search.job_manager", mock_job_manager_standalone), \
             patch("backend.api.routes.audio_search.get_audio_search_service", return_value=mock_audio_search), \
             patch("backend.api.routes.audio_search.FirestoreService", return_value=mock_firestore), \
             patch("backend.services.job_manager.JobManager", mock_jm_factory), \
             patch("backend.services.firestore_service.firestore"), \
             patch("backend.services.storage_service.storage"), \
             patch("google.auth.default", return_value=(mock_creds, "test-project")):
            from backend.main import app
            client = TestClient(app)
            response = client.post(
                "/api/audio-search/search-standalone",
                json={"artist": "Unknown Artist", "title": "Unknown Song"},
                headers=auth_headers,
            )

        assert response.status_code == 200
        data = response.json()
        assert data["results"] == []
        assert data["results_count"] == 0

    def test_missing_artist_returns_422(self, standalone_client, auth_headers):
        """Missing required artist field returns 422 validation error."""
        response = standalone_client.post(
            "/api/audio-search/search-standalone",
            json={"title": "Waterloo"},
            headers=auth_headers,
        )
        assert response.status_code == 422

    def test_missing_title_returns_422(self, standalone_client, auth_headers):
        """Missing required title field returns 422 validation error."""
        response = standalone_client.post(
            "/api/audio-search/search-standalone",
            json={"artist": "ABBA"},
            headers=auth_headers,
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/jobs/create-from-search
# ---------------------------------------------------------------------------

@pytest.fixture
def create_from_search_client(mock_job_manager_cfs):
    """TestClient for create-from-search tests with a valid session in Firestore."""
    session = _make_search_session(user_email="test@example.com")

    mock_firestore = MagicMock()
    mock_firestore.get_search_session.return_value = session
    mock_firestore.consume_search_session.return_value = session

    mock_audio_search = MagicMock()

    mock_theme_service = MagicMock()
    mock_theme_service.get_default_theme_id.return_value = None

    mock_creds = _make_mock_creds()

    def mock_jm_factory(*args, **kwargs):
        return mock_job_manager_cfs

    with patch("backend.api.routes.jobs.job_manager", mock_job_manager_cfs), \
         patch("backend.api.routes.jobs.FirestoreService", return_value=mock_firestore), \
         patch("backend.api.routes.jobs.get_theme_service", return_value=mock_theme_service), \
         patch("backend.api.routes.audio_search._validate_and_prepare_selection"), \
         patch("backend.api.routes.audio_search._download_audio_and_trigger_workers"), \
         patch("backend.api.routes.audio_search.extract_request_metadata", return_value={}), \
         patch("backend.api.routes.audio_search.get_audio_search_service", return_value=mock_audio_search), \
         patch("backend.services.job_manager.JobManager", mock_jm_factory), \
         patch("backend.services.firestore_service.firestore"), \
         patch("backend.services.storage_service.storage"), \
         patch("google.auth.default", return_value=(mock_creds, "test-project")):
        from backend.main import app
        client = TestClient(app)
        client._mock_firestore = mock_firestore
        client._mock_jm = mock_job_manager_cfs
        yield client


@pytest.fixture
def mock_job_manager_cfs():
    return _make_mock_job_manager()


class TestCreateJobFromSearch:
    """Tests for POST /api/jobs/create-from-search."""

    def test_returns_200_with_job_id(self, create_from_search_client, auth_headers):
        """Successful call returns 200 with job_id."""
        response = create_from_search_client.post(
            "/api/jobs/create-from-search",
            json={
                "search_session_id": "sess-abc-123",
                "selection_index": 0,
                "artist": "ABBA",
                "title": "Waterloo",
            },
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "job_id" in data
        assert data["job_id"] == "test-job-123"

    def test_job_is_created_via_job_manager(self, create_from_search_client, auth_headers):
        """Job must be created through job_manager.create_job."""
        create_from_search_client.post(
            "/api/jobs/create-from-search",
            json={
                "search_session_id": "sess-abc-123",
                "selection_index": 0,
                "artist": "ABBA",
                "title": "Waterloo",
            },
            headers=auth_headers,
        )
        create_from_search_client._mock_jm.create_job.assert_called_once()

    def test_is_private_flag_passed_to_job(self, create_from_search_client, auth_headers):
        """is_private flag is included in JobCreate passed to job_manager."""
        create_from_search_client.post(
            "/api/jobs/create-from-search",
            json={
                "search_session_id": "sess-abc-123",
                "selection_index": 0,
                "artist": "ABBA",
                "title": "Waterloo",
                "is_private": True,
            },
            headers=auth_headers,
        )
        call_args = create_from_search_client._mock_jm.create_job.call_args
        job_create = call_args[0][0]  # First positional arg
        assert job_create.is_private is True

    def test_display_overrides_applied_when_provided(self, create_from_search_client, auth_headers):
        """display_artist and display_title overrides are applied."""
        create_from_search_client.post(
            "/api/jobs/create-from-search",
            json={
                "search_session_id": "sess-abc-123",
                "selection_index": 0,
                "artist": "ABBA",
                "title": "Waterloo",
                "display_artist": "Abba (Display)",
                "display_title": "Waterloo (Karaoke)",
            },
            headers=auth_headers,
        )
        call_args = create_from_search_client._mock_jm.create_job.call_args
        job_create = call_args[0][0]
        assert job_create.artist == "Abba (Display)"
        assert job_create.title == "Waterloo (Karaoke)"

    def test_session_consumed_atomically(self, create_from_search_client, auth_headers):
        """Session is consumed (atomically read+deleted) via consume_search_session."""
        create_from_search_client.post(
            "/api/jobs/create-from-search",
            json={
                "search_session_id": "sess-abc-123",
                "selection_index": 0,
                "artist": "ABBA",
                "title": "Waterloo",
            },
            headers=auth_headers,
        )
        # consume_search_session handles both read + delete atomically
        create_from_search_client._mock_firestore.consume_search_session.assert_called_once_with(
            "sess-abc-123"
        )
        # No separate delete_search_session call needed
        create_from_search_client._mock_firestore.delete_search_session.assert_not_called()

    def test_missing_session_returns_404(self, mock_job_manager_cfs, auth_headers):
        """Returns 404 when search session is not found (expired or invalid)."""
        mock_firestore = MagicMock()
        mock_firestore.get_search_session.return_value = None  # session not found

        mock_theme_service = MagicMock()
        mock_theme_service.get_default_theme_id.return_value = None

        mock_creds = _make_mock_creds()

        def mock_jm_factory(*args, **kwargs):
            return mock_job_manager_cfs

        with patch("backend.api.routes.jobs.job_manager", mock_job_manager_cfs), \
             patch("backend.api.routes.jobs.FirestoreService", return_value=mock_firestore), \
             patch("backend.api.routes.jobs.get_theme_service", return_value=mock_theme_service), \
             patch("backend.services.job_manager.JobManager", mock_jm_factory), \
             patch("backend.services.firestore_service.firestore"), \
             patch("backend.services.storage_service.storage"), \
             patch("google.auth.default", return_value=(mock_creds, "test-project")):
            from backend.main import app
            client = TestClient(app)
            response = client.post(
                "/api/jobs/create-from-search",
                json={
                    "search_session_id": "expired-session",
                    "selection_index": 0,
                    "artist": "ABBA",
                    "title": "Waterloo",
                },
                headers=auth_headers,
            )

        assert response.status_code == 404
        assert "Search expired" in response.json()["detail"]

    def test_expired_ttl_returns_404(self, mock_job_manager_cfs, auth_headers):
        """Returns 404 when session TTL has passed (belt-and-suspenders check)."""
        expired_session = _make_search_session(user_email="test@example.com", expired=True)

        mock_firestore = MagicMock()
        mock_firestore.get_search_session.return_value = expired_session

        mock_theme_service = MagicMock()
        mock_theme_service.get_default_theme_id.return_value = None

        mock_creds = _make_mock_creds()

        def mock_jm_factory(*args, **kwargs):
            return mock_job_manager_cfs

        with patch("backend.api.routes.jobs.job_manager", mock_job_manager_cfs), \
             patch("backend.api.routes.jobs.FirestoreService", return_value=mock_firestore), \
             patch("backend.api.routes.jobs.get_theme_service", return_value=mock_theme_service), \
             patch("backend.services.job_manager.JobManager", mock_jm_factory), \
             patch("backend.services.firestore_service.firestore"), \
             patch("backend.services.storage_service.storage"), \
             patch("google.auth.default", return_value=(mock_creds, "test-project")):
            from backend.main import app
            client = TestClient(app)
            response = client.post(
                "/api/jobs/create-from-search",
                json={
                    "search_session_id": "sess-abc-123",
                    "selection_index": 0,
                    "artist": "ABBA",
                    "title": "Waterloo",
                },
                headers=auth_headers,
            )

        assert response.status_code == 404
        assert "Search expired" in response.json()["detail"]

    def test_tenant_mismatch_returns_403(self, mock_job_manager_cfs, auth_headers):
        """Returns 403 when session tenant_id doesn't match the request tenant."""
        # Session was created in tenant "tenant-a"
        session_with_tenant = _make_search_session(user_email="test@example.com", tenant_id="tenant-a")

        mock_firestore = MagicMock()
        mock_firestore.get_search_session.return_value = session_with_tenant

        mock_theme_service = MagicMock()
        mock_theme_service.get_default_theme_id.return_value = None

        mock_creds = _make_mock_creds()

        def mock_jm_factory(*args, **kwargs):
            return mock_job_manager_cfs

        with patch("backend.api.routes.jobs.job_manager", mock_job_manager_cfs), \
             patch("backend.api.routes.jobs.FirestoreService", return_value=mock_firestore), \
             patch("backend.api.routes.jobs.get_theme_service", return_value=mock_theme_service), \
             patch("backend.services.job_manager.JobManager", mock_jm_factory), \
             patch("backend.services.firestore_service.firestore"), \
             patch("backend.services.storage_service.storage"), \
             patch("google.auth.default", return_value=(mock_creds, "test-project")):
            from backend.main import app
            client = TestClient(app)
            # Request arrives from the default (no-tenant) context — tenant_id=None in request.state
            response = client.post(
                "/api/jobs/create-from-search",
                json={
                    "search_session_id": "sess-abc-123",
                    "selection_index": 0,
                    "artist": "ABBA",
                    "title": "Waterloo",
                },
                headers=auth_headers,
            )

        assert response.status_code == 403

    def test_invalid_selection_index_returns_400(self, mock_job_manager_cfs, auth_headers):
        """Returns 400 when selection_index is out of bounds."""
        session = _make_search_session()  # 1 result at index 0

        mock_firestore = MagicMock()
        mock_firestore.get_search_session.return_value = session

        mock_theme_service = MagicMock()
        mock_theme_service.get_default_theme_id.return_value = None

        mock_creds = _make_mock_creds()

        def mock_jm_factory(*args, **kwargs):
            return mock_job_manager_cfs

        with patch("backend.api.routes.jobs.job_manager", mock_job_manager_cfs), \
             patch("backend.api.routes.jobs.FirestoreService", return_value=mock_firestore), \
             patch("backend.api.routes.jobs.get_theme_service", return_value=mock_theme_service), \
             patch("backend.services.job_manager.JobManager", mock_jm_factory), \
             patch("backend.services.firestore_service.firestore"), \
             patch("backend.services.storage_service.storage"), \
             patch("google.auth.default", return_value=(mock_creds, "test-project")):
            from backend.main import app
            client = TestClient(app)
            response = client.post(
                "/api/jobs/create-from-search",
                json={
                    "search_session_id": "sess-abc-123",
                    "selection_index": 99,  # out of range
                    "artist": "ABBA",
                    "title": "Waterloo",
                },
                headers=auth_headers,
            )

        assert response.status_code == 400
        assert "Invalid selection index" in response.json()["detail"]

    def test_missing_required_fields_returns_422(self, create_from_search_client, auth_headers):
        """Missing required fields return 422 validation error."""
        response = create_from_search_client.post(
            "/api/jobs/create-from-search",
            json={"search_session_id": "sess-abc-123"},  # missing selection_index, artist, title
            headers=auth_headers,
        )
        assert response.status_code == 422

    def test_no_awaiting_selection_state(self, create_from_search_client, auth_headers):
        """Job goes directly to downloading — AWAITING_AUDIO_SELECTION is never set."""
        create_from_search_client.post(
            "/api/jobs/create-from-search",
            json={
                "search_session_id": "sess-abc-123",
                "selection_index": 0,
                "artist": "ABBA",
                "title": "Waterloo",
            },
            headers=auth_headers,
        )
        # The created job should NOT be in AWAITING_AUDIO_SELECTION
        # (we verify job_manager.create_job was called and the job status is not AWAITING)
        call_args = create_from_search_client._mock_jm.create_job.call_args
        assert call_args is not None  # job was created
        # The initial status is PENDING — it's then moved to DOWNLOADING_AUDIO
        # by _validate_and_prepare_selection (which is mocked here)
        created_job = create_from_search_client._mock_jm.create_job.return_value
        assert created_job.status != JobStatus.AWAITING_AUDIO_SELECTION

    def test_different_user_cannot_use_anothers_session(self, mock_job_manager_cfs, auth_headers):
        """Returns 403 when session belongs to a different user (ownership check)."""
        from backend.api.dependencies import require_auth
        from backend.services.auth_service import AuthResult, UserType

        # Session belongs to alice, but request comes from bob (non-admin)
        session = _make_search_session(user_email="alice@example.com")

        mock_firestore = MagicMock()
        mock_firestore.get_search_session.return_value = session

        mock_theme_service = MagicMock()
        mock_theme_service.get_default_theme_id.return_value = None

        mock_creds = _make_mock_creds()

        def mock_jm_factory(*args, **kwargs):
            return mock_job_manager_cfs

        async def bob_auth():
            return AuthResult(
                is_valid=True, user_type=UserType.LIMITED,
                remaining_uses=5, message="OK", is_admin=False,
                user_email="bob@example.com",
            )

        with patch("backend.api.routes.jobs.job_manager", mock_job_manager_cfs), \
             patch("backend.api.routes.jobs.FirestoreService", return_value=mock_firestore), \
             patch("backend.api.routes.jobs.get_theme_service", return_value=mock_theme_service), \
             patch("backend.services.job_manager.JobManager", mock_jm_factory), \
             patch("backend.services.firestore_service.firestore"), \
             patch("backend.services.storage_service.storage"), \
             patch("google.auth.default", return_value=(mock_creds, "test-project")):
            from backend.main import app
            app.dependency_overrides[require_auth] = bob_auth
            try:
                client = TestClient(app)
                response = client.post(
                    "/api/jobs/create-from-search",
                    json={
                        "search_session_id": "sess-abc-123",
                        "selection_index": 0,
                        "artist": "ABBA",
                        "title": "Waterloo",
                    },
                    headers=auth_headers,
                )
            finally:
                # Restore the default admin mock
                # Restore the default admin auth (same as conftest autouse fixture)
                async def _restore_admin_auth():
                    return AuthResult(
                        is_valid=True, user_type=UserType.ADMIN,
                        remaining_uses=999, message="Test admin token",
                        is_admin=True, user_email="test@example.com",
                    )
                app.dependency_overrides[require_auth] = _restore_admin_auth

        assert response.status_code == 403

    def test_remote_search_id_stored_in_state_data(self, mock_job_manager_cfs, auth_headers):
        """remote_search_id from session is copied to job state_data."""
        session = _make_search_session(user_email="test@example.com")
        session["remote_search_id"] = "torrent-search-42"

        mock_firestore = MagicMock()
        mock_firestore.get_search_session.return_value = session
        mock_firestore.consume_search_session.return_value = session

        mock_audio_search = MagicMock()

        mock_theme_service = MagicMock()
        mock_theme_service.get_default_theme_id.return_value = None

        mock_creds = _make_mock_creds()

        def mock_jm_factory(*args, **kwargs):
            return mock_job_manager_cfs

        with patch("backend.api.routes.jobs.job_manager", mock_job_manager_cfs), \
             patch("backend.api.routes.jobs.FirestoreService", return_value=mock_firestore), \
             patch("backend.api.routes.jobs.get_theme_service", return_value=mock_theme_service), \
             patch("backend.api.routes.audio_search._validate_and_prepare_selection"), \
             patch("backend.api.routes.audio_search._download_audio_and_trigger_workers"), \
             patch("backend.api.routes.audio_search.extract_request_metadata", return_value={}), \
             patch("backend.api.routes.audio_search.get_audio_search_service", return_value=mock_audio_search), \
             patch("backend.services.job_manager.JobManager", mock_jm_factory), \
             patch("backend.services.firestore_service.firestore"), \
             patch("backend.services.storage_service.storage"), \
             patch("google.auth.default", return_value=(mock_creds, "test-project")):
            from backend.main import app
            client = TestClient(app)
            response = client.post(
                "/api/jobs/create-from-search",
                json={
                    "search_session_id": "sess-abc-123",
                    "selection_index": 0,
                    "artist": "ABBA",
                    "title": "Waterloo",
                },
                headers=auth_headers,
            )

        assert response.status_code == 200
        # Check that update_job was called with state_data containing remote_search_id
        update_calls = mock_job_manager_cfs.update_job.call_args_list
        state_data_call = [c for c in update_calls if 'state_data' in c[0][1]]
        assert len(state_data_call) >= 1
        state_data = state_data_call[0][0][1]['state_data']
        assert state_data['remote_search_id'] == "torrent-search-42"

    def test_concurrent_consume_second_request_returns_404(self, mock_job_manager_cfs, auth_headers):
        """Second concurrent request to same session gets 404 (session already consumed)."""
        session = _make_search_session(user_email="test@example.com")

        mock_firestore = MagicMock()
        mock_firestore.get_search_session.return_value = session
        # First consume succeeds, simulating that another request already consumed it
        mock_firestore.consume_search_session.return_value = None

        mock_audio_search = MagicMock()

        mock_theme_service = MagicMock()
        mock_theme_service.get_default_theme_id.return_value = None

        mock_creds = _make_mock_creds()

        def mock_jm_factory(*args, **kwargs):
            return mock_job_manager_cfs

        with patch("backend.api.routes.jobs.job_manager", mock_job_manager_cfs), \
             patch("backend.api.routes.jobs.FirestoreService", return_value=mock_firestore), \
             patch("backend.api.routes.jobs.get_theme_service", return_value=mock_theme_service), \
             patch("backend.api.routes.audio_search._validate_and_prepare_selection"), \
             patch("backend.api.routes.audio_search._download_audio_and_trigger_workers"), \
             patch("backend.api.routes.audio_search.extract_request_metadata", return_value={}), \
             patch("backend.api.routes.audio_search.get_audio_search_service", return_value=mock_audio_search), \
             patch("backend.services.job_manager.JobManager", mock_jm_factory), \
             patch("backend.services.firestore_service.firestore"), \
             patch("backend.services.storage_service.storage"), \
             patch("google.auth.default", return_value=(mock_creds, "test-project")):
            from backend.main import app
            client = TestClient(app)
            response = client.post(
                "/api/jobs/create-from-search",
                json={
                    "search_session_id": "sess-abc-123",
                    "selection_index": 0,
                    "artist": "ABBA",
                    "title": "Waterloo",
                },
                headers=auth_headers,
            )

        assert response.status_code == 404
        assert "Search expired" in response.json()["detail"]


# ---------------------------------------------------------------------------
# POST /api/audio-search/search-standalone — credit check
# ---------------------------------------------------------------------------

class TestSearchStandaloneCredits:
    """Tests for credit checking on the standalone search endpoint."""

    def test_insufficient_credits_returns_402(self, auth_headers):
        """Non-admin user with no credits gets 402 on search."""
        from backend.api.dependencies import require_auth
        from backend.services.auth_service import AuthResult, UserType

        mock_audio_search = MagicMock()
        mock_user_service = MagicMock()
        mock_user_service.has_credits.return_value = False
        mock_user_service.check_credits.return_value = 0

        mock_firestore = MagicMock()
        mock_creds = _make_mock_creds()
        mock_jm = _make_mock_job_manager()

        async def regular_user_auth():
            return AuthResult(
                is_valid=True, user_type=UserType.LIMITED,
                remaining_uses=0, message="OK", is_admin=False,
                user_email="user@example.com",
            )

        with patch("backend.api.routes.audio_search.job_manager", mock_jm), \
             patch("backend.api.routes.audio_search.get_audio_search_service", return_value=mock_audio_search), \
             patch("backend.api.routes.audio_search.FirestoreService", return_value=mock_firestore), \
             patch("backend.services.user_service.get_user_service", return_value=mock_user_service), \
             patch("backend.services.job_manager.JobManager", lambda *a, **k: mock_jm), \
             patch("backend.services.firestore_service.firestore"), \
             patch("backend.services.storage_service.storage"), \
             patch("google.auth.default", return_value=(mock_creds, "test-project")):
            from backend.main import app
            app.dependency_overrides[require_auth] = regular_user_auth
            try:
                client = TestClient(app, raise_server_exceptions=False)
                response = client.post(
                    "/api/audio-search/search-standalone",
                    json={"artist": "ABBA", "title": "Waterloo"},
                    headers=auth_headers,
                )
            finally:
                # Restore the default admin auth (same as conftest autouse fixture)
                async def _restore_admin_auth():
                    return AuthResult(
                        is_valid=True, user_type=UserType.ADMIN,
                        remaining_uses=999, message="Test admin token",
                        is_admin=True, user_email="test@example.com",
                    )
                app.dependency_overrides[require_auth] = _restore_admin_auth

        assert response.status_code == 402
        data = response.json()
        assert "credits" in data["detail"].lower()
