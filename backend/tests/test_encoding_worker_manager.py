"""
Tests for the blue-green encoding worker manager service.

Tests cover:
- Task 1: Firestore config document schema, get_config, update_activity, swap_roles
- Task 2: VM lifecycle (get_vm_status, start_vm, stop_vm, ensure_primary_running)
"""

import pytest
import aiohttp
from datetime import datetime, UTC
from unittest.mock import MagicMock, patch, call, AsyncMock

from backend.services.encoding_worker_manager import (
    EncodingWorkerConfig,
    EncodingWorkerManager,
    CONFIG_COLLECTION,
    CONFIG_DOCUMENT,
    ENCODING_WORKER_PORT,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_firestore_data(**overrides):
    """Create a realistic Firestore document dict for encoding-worker config."""
    data = {
        "primary_vm": "encoding-worker-blue",
        "primary_ip": "10.128.0.50",
        "primary_version": "1.0.0",
        "primary_deployed_at": "2026-03-20T10:00:00Z",
        "secondary_vm": "encoding-worker-green",
        "secondary_ip": "10.128.0.51",
        "secondary_version": "0.9.0",
        "secondary_deployed_at": "2026-03-19T08:00:00Z",
        "last_swap_at": "2026-03-20T10:00:00Z",
        "last_activity_at": "2026-03-24T12:00:00Z",
        "deploy_in_progress": False,
        "deploy_in_progress_since": None,
    }
    data.update(overrides)
    return data


@pytest.fixture
def mock_db():
    """Return a mock Firestore client with a pre-configured config document."""
    db = MagicMock()
    snapshot = MagicMock()
    snapshot.exists = True
    snapshot.to_dict.return_value = _make_firestore_data()

    doc_ref = MagicMock()
    doc_ref.get.return_value = snapshot

    collection_ref = MagicMock()
    collection_ref.document.return_value = doc_ref

    db.collection.return_value = collection_ref
    return db


@pytest.fixture
def mock_compute():
    """Return a mock Compute Engine InstancesClient."""
    return MagicMock()


@pytest.fixture
def manager(mock_db, mock_compute):
    return EncodingWorkerManager(
        db=mock_db,
        compute_client=mock_compute,
        project_id="nomadkaraoke",
        zone="us-central1-c",
    )


# ---------------------------------------------------------------------------
# Task 1: Config dataclass
# ---------------------------------------------------------------------------

class TestEncodingWorkerConfig:
    """Tests for the EncodingWorkerConfig dataclass."""

    def test_primary_url(self):
        config = EncodingWorkerConfig(
            primary_vm="blue", primary_ip="10.0.0.1", primary_version="1.0",
            primary_deployed_at=None,
            secondary_vm="green", secondary_ip="10.0.0.2",
            secondary_version=None, secondary_deployed_at=None,
            last_swap_at=None, last_activity_at=None,
            deploy_in_progress=False, deploy_in_progress_since=None,
        )
        assert config.primary_url == "http://10.0.0.1:8080"

    def test_secondary_url(self):
        config = EncodingWorkerConfig(
            primary_vm="blue", primary_ip="10.0.0.1", primary_version="1.0",
            primary_deployed_at=None,
            secondary_vm="green", secondary_ip="10.0.0.2",
            secondary_version=None, secondary_deployed_at=None,
            last_swap_at=None, last_activity_at=None,
            deploy_in_progress=False, deploy_in_progress_since=None,
        )
        assert config.secondary_url == "http://10.0.0.2:8080"


# ---------------------------------------------------------------------------
# Task 1: get_config / update_activity / swap_roles
# ---------------------------------------------------------------------------

class TestEncodingWorkerManager:
    """Tests for Firestore config operations."""

    def test_get_config_returns_dataclass(self, manager, mock_db):
        config = manager.get_config()
        assert isinstance(config, EncodingWorkerConfig)
        assert config.primary_vm == "encoding-worker-blue"
        assert config.primary_ip == "10.128.0.50"
        assert config.primary_version == "1.0.0"
        assert config.secondary_vm == "encoding-worker-green"
        assert config.deploy_in_progress is False

    def test_get_config_document_not_found(self, manager, mock_db):
        snapshot = MagicMock()
        snapshot.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = snapshot

        with pytest.raises(ValueError, match="not found"):
            manager.get_config()

    def test_get_config_reads_correct_path(self, manager, mock_db):
        manager.get_config()
        mock_db.collection.assert_called_with(CONFIG_COLLECTION)
        mock_db.collection.return_value.document.assert_called_with(CONFIG_DOCUMENT)

    def test_update_activity_sets_timestamp(self, manager, mock_db):
        with patch("backend.services.encoding_worker_manager.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 24, 15, 0, 0, tzinfo=UTC)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            manager.update_activity()

        doc_ref = mock_db.collection.return_value.document.return_value
        doc_ref.update.assert_called_once_with({
            "last_activity_at": "2026-03-24T15:00:00+00:00",
        })

    def test_swap_roles_uses_transaction(self, manager, mock_db):
        """swap_roles should use a Firestore transaction for atomicity."""
        # Set up the transaction mock
        mock_transaction = MagicMock()
        mock_db.transaction.return_value = mock_transaction

        # The transactional function is called with the transaction
        manager.swap_roles(
            new_primary_vm="encoding-worker-green",
            new_primary_ip="10.128.0.51",
            new_primary_version="2.0.0",
            old_primary_vm="encoding-worker-blue",
            old_primary_ip="10.128.0.50",
            old_primary_version="1.0.0",
        )

        # Verify transaction was created and the doc was updated within it
        mock_db.transaction.assert_called_once()

    def test_swap_roles_writes_correct_data(self, manager, mock_db):
        """swap_roles should write the swapped primary/secondary fields."""
        mock_transaction = MagicMock()
        mock_db.transaction.return_value = mock_transaction

        with patch("backend.services.encoding_worker_manager.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 24, 16, 0, 0, tzinfo=UTC)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            manager.swap_roles(
                new_primary_vm="encoding-worker-green",
                new_primary_ip="10.128.0.51",
                new_primary_version="2.0.0",
                old_primary_vm="encoding-worker-blue",
                old_primary_ip="10.128.0.50",
                old_primary_version="1.0.0",
            )

        # The transaction.update call should contain the swapped values
        doc_ref = mock_db.collection.return_value.document.return_value
        update_call = mock_transaction.update.call_args
        assert update_call is not None
        data = update_call[0][1]  # second positional arg is the data dict
        assert data["primary_vm"] == "encoding-worker-green"
        assert data["primary_ip"] == "10.128.0.51"
        assert data["primary_version"] == "2.0.0"
        assert data["secondary_vm"] == "encoding-worker-blue"
        assert data["secondary_ip"] == "10.128.0.50"
        assert data["secondary_version"] == "1.0.0"
        assert data["last_swap_at"] == "2026-03-24T16:00:00+00:00"


# ---------------------------------------------------------------------------
# Task 2: VM lifecycle
# ---------------------------------------------------------------------------

class TestVMLifecycle:
    """Tests for Compute Engine VM operations."""

    def test_get_vm_status_returns_status(self, manager, mock_compute):
        mock_instance = MagicMock()
        mock_instance.status = "RUNNING"
        mock_compute.get.return_value = mock_instance

        status = manager.get_vm_status("encoding-worker-blue")
        assert status == "RUNNING"
        mock_compute.get.assert_called_once_with(
            project="nomadkaraoke",
            zone="us-central1-c",
            instance="encoding-worker-blue",
        )

    def test_get_vm_status_terminated(self, manager, mock_compute):
        mock_instance = MagicMock()
        mock_instance.status = "TERMINATED"
        mock_compute.get.return_value = mock_instance

        assert manager.get_vm_status("encoding-worker-blue") == "TERMINATED"

    def test_start_vm_calls_compute_start(self, manager, mock_compute):
        mock_instance = MagicMock()
        mock_instance.status = "TERMINATED"
        mock_compute.get.return_value = mock_instance

        manager.start_vm("encoding-worker-blue")
        mock_compute.start.assert_called_once_with(
            project="nomadkaraoke",
            zone="us-central1-c",
            instance="encoding-worker-blue",
        )

    def test_start_vm_skips_if_already_running(self, manager, mock_compute):
        mock_instance = MagicMock()
        mock_instance.status = "RUNNING"
        mock_compute.get.return_value = mock_instance

        manager.start_vm("encoding-worker-blue")
        mock_compute.start.assert_not_called()

    def test_stop_vm_calls_compute_stop(self, manager, mock_compute):
        mock_instance = MagicMock()
        mock_instance.status = "RUNNING"
        mock_compute.get.return_value = mock_instance

        manager.stop_vm("encoding-worker-blue")
        mock_compute.stop.assert_called_once_with(
            project="nomadkaraoke",
            zone="us-central1-c",
            instance="encoding-worker-blue",
        )

    def test_stop_vm_skips_if_already_terminated(self, manager, mock_compute):
        mock_instance = MagicMock()
        mock_instance.status = "TERMINATED"
        mock_compute.get.return_value = mock_instance

        manager.stop_vm("encoding-worker-blue")
        mock_compute.stop.assert_not_called()

    def test_ensure_primary_running_starts_stopped_vm(self, manager, mock_db, mock_compute):
        """When primary is stopped, ensure_primary_running should start it."""
        mock_instance = MagicMock()
        mock_instance.status = "TERMINATED"
        mock_compute.get.return_value = mock_instance

        with patch("backend.services.encoding_worker_manager.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 24, 15, 0, 0, tzinfo=UTC)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = manager.ensure_primary_running()

        assert result["started"] is True
        assert result["vm_name"] == "encoding-worker-blue"
        assert result["primary_url"] == "http://10.128.0.50:8080"
        mock_compute.start.assert_called_once()

    def test_ensure_primary_running_already_running(self, manager, mock_db, mock_compute):
        """When primary is already running, should not start and still update activity."""
        mock_instance = MagicMock()
        mock_instance.status = "RUNNING"
        mock_compute.get.return_value = mock_instance

        with patch("backend.services.encoding_worker_manager.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 24, 15, 0, 0, tzinfo=UTC)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = manager.ensure_primary_running()

        assert result["started"] is False
        assert result["vm_name"] == "encoding-worker-blue"
        assert result["primary_url"] == "http://10.128.0.50:8080"
        mock_compute.start.assert_not_called()

    def test_ensure_primary_running_updates_activity(self, manager, mock_db, mock_compute):
        """ensure_primary_running should always update last_activity_at."""
        mock_instance = MagicMock()
        mock_instance.status = "RUNNING"
        mock_compute.get.return_value = mock_instance

        with patch("backend.services.encoding_worker_manager.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 24, 15, 0, 0, tzinfo=UTC)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            manager.ensure_primary_running()

        doc_ref = mock_db.collection.return_value.document.return_value
        doc_ref.update.assert_called_once()

    def test_start_vm_skips_if_staging(self, manager, mock_compute):
        """start_vm should also skip if VM is in STAGING state (already starting)."""
        mock_instance = MagicMock()
        mock_instance.status = "STAGING"
        mock_compute.get.return_value = mock_instance

        manager.start_vm("encoding-worker-blue")
        mock_compute.start.assert_not_called()

    def test_stop_vm_skips_if_stopping(self, manager, mock_compute):
        """stop_vm should skip if VM is already in STOPPING state."""
        mock_instance = MagicMock()
        mock_instance.status = "STOPPING"
        mock_compute.get.return_value = mock_instance

        manager.stop_vm("encoding-worker-blue")
        mock_compute.stop.assert_not_called()


# ---------------------------------------------------------------------------
# Task 3: wait_for_worker_ready (cold-start readiness gate)
# ---------------------------------------------------------------------------


class TestWaitForWorkerReady:
    """Tests for the wait_for_worker_ready() readiness gate."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _mk_health_session(get_callable):
        """Build a mock ClientSession whose .get(url, ...) returns an async-CM Resp."""
        mock_session = MagicMock()
        mock_session.get = get_callable
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        return mock_session

    @staticmethod
    def _mk_resp_cm(status):
        """Return a callable that, when invoked with a URL, returns an async-CM Resp."""
        class _Resp:
            def __init__(self, s):
                self.status = s
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None
        def _get(url, **kwargs):
            return _Resp(status)
        return _get

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_returns_immediately_when_vm_running_and_healthy(self, manager, mock_compute):
        """Happy path: VM already RUNNING, /health returns 200 on first poll."""
        mock_instance = MagicMock()
        mock_instance.status = "RUNNING"
        mock_compute.get.return_value = mock_instance

        mock_session = self._mk_health_session(self._mk_resp_cm(200))

        with patch("backend.services.encoding_worker_manager.aiohttp.ClientSession", return_value=mock_session):
            await manager.wait_for_worker_ready(
                "encoding-worker-blue",
                "http://10.0.0.1:8080/health",
                vm_timeout=2.0,
                health_timeout=2.0,
                poll_interval=0.05,
            )

        # Single VM status check, single health check
        assert mock_compute.get.call_count == 1

    @pytest.mark.asyncio
    async def test_polls_vm_until_running(self, manager, mock_compute):
        """Polls VM status through STAGING → RUNNING."""
        statuses = ["STAGING", "STAGING", "RUNNING"]
        instances = [MagicMock(status=s) for s in statuses]
        mock_compute.get.side_effect = instances

        mock_session = self._mk_health_session(self._mk_resp_cm(200))

        with patch("backend.services.encoding_worker_manager.aiohttp.ClientSession", return_value=mock_session):
            await manager.wait_for_worker_ready(
                "encoding-worker-blue",
                "http://10.0.0.1:8080/health",
                vm_timeout=2.0,
                health_timeout=2.0,
                poll_interval=0.05,
            )

        assert mock_compute.get.call_count == 3

    @pytest.mark.asyncio
    async def test_times_out_when_vm_never_runs(self, manager, mock_compute):
        """Raises TimeoutError if VM stays in STAGING past vm_timeout."""
        mock_instance = MagicMock()
        mock_instance.status = "STAGING"
        mock_compute.get.return_value = mock_instance

        with pytest.raises(TimeoutError, match="did not reach RUNNING"):
            await manager.wait_for_worker_ready(
                "encoding-worker-blue",
                "http://10.0.0.1:8080/health",
                vm_timeout=0.2,
                health_timeout=2.0,
                poll_interval=0.05,
            )

    @pytest.mark.asyncio
    async def test_times_out_when_health_never_200(self, manager, mock_compute):
        """Raises TimeoutError if /health never returns 200 past health_timeout."""
        mock_instance = MagicMock()
        mock_instance.status = "RUNNING"
        mock_compute.get.return_value = mock_instance

        mock_session = self._mk_health_session(self._mk_resp_cm(503))

        with patch("backend.services.encoding_worker_manager.aiohttp.ClientSession", return_value=mock_session):
            with pytest.raises(TimeoutError, match="did not become healthy"):
                await manager.wait_for_worker_ready(
                    "encoding-worker-blue",
                    "http://10.0.0.1:8080/health",
                    vm_timeout=2.0,
                    health_timeout=0.2,
                    poll_interval=0.05,
                )

    @pytest.mark.asyncio
    async def test_tolerates_health_connection_errors(self, manager, mock_compute):
        """Connection errors during /health polling are treated as 'not ready', keep polling."""
        mock_instance = MagicMock()
        mock_instance.status = "RUNNING"
        mock_compute.get.return_value = mock_instance

        call_count = {"n": 0}

        class _OkResp:
            status = 200
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return None

        def flaky_get(url, **kwargs):
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise aiohttp.ClientConnectorError(MagicMock(), OSError("connection refused"))
            return _OkResp()

        mock_session = self._mk_health_session(flaky_get)

        with patch("backend.services.encoding_worker_manager.aiohttp.ClientSession", return_value=mock_session):
            await manager.wait_for_worker_ready(
                "encoding-worker-blue",
                "http://10.0.0.1:8080/health",
                vm_timeout=2.0,
                health_timeout=2.0,
                poll_interval=0.05,
            )

        assert call_count["n"] == 3
