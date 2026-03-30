# Blue-Green Encoding Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single encoding worker VM with a blue-green pair that auto-starts on demand, deploys with deep health checks, and shuts down when idle.

**Architecture:** Two named GCE VMs (a/b) managed by Pulumi. Backend reads the active primary from a Firestore config doc and starts it on demand. CI deploys to the secondary, runs a real encode test, then swaps the Firestore pointer atomically. A Cloud Function shuts down idle VMs every 5 minutes.

**Tech Stack:** Pulumi (Python), FastAPI, Next.js/TypeScript, GCE Compute API, Firestore, Cloud Functions (Gen2), Cloud Scheduler, GitHub Actions

**Spec:** `docs/superpowers/specs/2026-03-24-blue-green-encoding-worker-design.md`

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `backend/services/encoding_worker_manager.py` | VM lifecycle: start, stop, health check, Firestore config reads/writes |
| `backend/api/routes/encoding_worker.py` | `/api/internal/encoding-worker/warmup` and `/heartbeat` endpoints |
| `backend/tests/test_encoding_worker_manager.py` | Unit tests for VM manager service |
| `backend/tests/test_encoding_worker_routes.py` | Unit tests for warmup/heartbeat endpoints |
| `infrastructure/functions/encoding_worker_idle/main.py` | Cloud Function: idle auto-shutdown |
| `infrastructure/functions/encoding_worker_idle/requirements.txt` | Cloud Function dependencies |
| `infrastructure/modules/encoding_worker_manager.py` | Pulumi module: Cloud Function + Cloud Scheduler for idle shutdown |

### Modified Files

| File | Changes |
|------|---------|
| `infrastructure/compute/encoding_worker_vm.py` | Two VMs (a/b) with two static IPs instead of one |
| `infrastructure/config.py` | Add `EncodingWorkerConfig` class |
| `infrastructure/__main__.py` | Wire up new VMs, Cloud Function, exports |
| `infrastructure/modules/iam/worker_sas.py` | Add `compute.instances.get/start` for backend SA |
| `backend/services/encoding_service.py` | Dynamic URL from Firestore (not cached), warmup-aware retry |
| `backend/config.py` | Remove static `encoding_worker_url`, add Firestore config path |
| `backend/main.py` | Register new encoding worker router |
| `.github/workflows/ci.yml` | Blue-green deploy flow replacing current restart-and-verify |
| `frontend/components/lyrics-review/LyricsAnalyzer.tsx` | Warmup signal on mount, debounced heartbeat |
| `frontend/components/lyrics-review/PreviewVideoSection.tsx` | "Warming up" state handling |
| `frontend/lib/api.ts` | `warmupEncodingWorker()` and `heartbeatEncodingWorker()` methods |

---

## Task 1: Firestore Config Document Schema + Seeding Script

**Files:**
- Create: `backend/services/encoding_worker_manager.py`
- Create: `backend/tests/test_encoding_worker_manager.py`

This task creates the core service that reads/writes the Firestore config document, which is the single source of truth for which VM is primary.

- [ ] **Step 1.1: Write failing test for reading config from Firestore**

```python
# backend/tests/test_encoding_worker_manager.py
import pytest
from unittest.mock import MagicMock, patch
from backend.services.encoding_worker_manager import EncodingWorkerManager, EncodingWorkerConfig


class TestEncodingWorkerManager:
    """Tests for the encoding worker manager service."""

    def setup_method(self):
        self.mock_db = MagicMock()
        self.manager = EncodingWorkerManager(db=self.mock_db)

    def test_get_config_returns_parsed_config(self):
        """Should read config/encoding-worker doc and return typed config."""
        self.mock_db.collection.return_value.document.return_value.get.return_value.to_dict.return_value = {
            "primary_vm": "encoding-worker-a",
            "primary_ip": "34.1.2.3",
            "primary_version": "0.152.0",
            "primary_deployed_at": "2026-03-24T16:00:00Z",
            "secondary_vm": "encoding-worker-b",
            "secondary_ip": "35.4.5.6",
            "secondary_version": "0.151.0",
            "secondary_deployed_at": "2026-03-23T12:00:00Z",
            "last_swap_at": "2026-03-24T16:00:00Z",
            "last_activity_at": "2026-03-24T15:45:00Z",
            "deploy_in_progress": False,
            "deploy_in_progress_since": None,
        }
        self.mock_db.collection.return_value.document.return_value.get.return_value.exists = True

        config = self.manager.get_config()

        assert config.primary_vm == "encoding-worker-a"
        assert config.primary_ip == "34.1.2.3"
        assert config.primary_url == "http://34.1.2.3:8080"
        assert config.secondary_vm == "encoding-worker-b"
        assert config.deploy_in_progress is False
        self.mock_db.collection.assert_called_with("config")

    def test_get_config_missing_doc_raises(self):
        """Should raise if config doc doesn't exist."""
        self.mock_db.collection.return_value.document.return_value.get.return_value.exists = False

        with pytest.raises(RuntimeError, match="Encoding worker config not found"):
            self.manager.get_config()
```

- [ ] **Step 1.2: Run test to verify it fails**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-blue-green-encoding-deploy && python -m pytest backend/tests/test_encoding_worker_manager.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 1.3: Implement EncodingWorkerConfig dataclass and get_config**

```python
# backend/services/encoding_worker_manager.py
"""
Encoding Worker Manager - VM lifecycle and Firestore config management.

Manages the blue-green encoding worker VMs:
- Reads/writes the Firestore config document (config/encoding-worker)
- Starts VMs on demand via Compute API
- Tracks activity timestamps for idle shutdown
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from google.cloud import firestore

CONFIG_COLLECTION = "config"
CONFIG_DOCUMENT = "encoding-worker"
ENCODING_WORKER_PORT = 8080


@dataclass
class EncodingWorkerConfig:
    """Parsed encoding worker config from Firestore."""

    primary_vm: str
    primary_ip: str
    primary_version: str
    primary_deployed_at: Optional[str]
    secondary_vm: str
    secondary_ip: str
    secondary_version: Optional[str]
    secondary_deployed_at: Optional[str]
    last_swap_at: Optional[str]
    last_activity_at: Optional[str]
    deploy_in_progress: bool
    deploy_in_progress_since: Optional[str]

    @property
    def primary_url(self) -> str:
        return f"http://{self.primary_ip}:{ENCODING_WORKER_PORT}"

    @property
    def secondary_url(self) -> str:
        return f"http://{self.secondary_ip}:{ENCODING_WORKER_PORT}"


class EncodingWorkerManager:
    """Manages encoding worker VM lifecycle and config."""

    def __init__(self, db: firestore.Client):
        self._db = db

    def get_config(self) -> EncodingWorkerConfig:
        """Read the encoding worker config from Firestore."""
        doc = self._db.collection(CONFIG_COLLECTION).document(CONFIG_DOCUMENT).get()
        if not doc.exists:
            raise RuntimeError(
                f"Encoding worker config not found at {CONFIG_COLLECTION}/{CONFIG_DOCUMENT}. "
                "Run the seeding script to initialize it."
            )
        data = doc.to_dict()
        return EncodingWorkerConfig(
            primary_vm=data["primary_vm"],
            primary_ip=data["primary_ip"],
            primary_version=data.get("primary_version", "unknown"),
            primary_deployed_at=data.get("primary_deployed_at"),
            secondary_vm=data["secondary_vm"],
            secondary_ip=data["secondary_ip"],
            secondary_version=data.get("secondary_version"),
            secondary_deployed_at=data.get("secondary_deployed_at"),
            last_swap_at=data.get("last_swap_at"),
            last_activity_at=data.get("last_activity_at"),
            deploy_in_progress=data.get("deploy_in_progress", False),
            deploy_in_progress_since=data.get("deploy_in_progress_since"),
        )

    def update_activity(self) -> None:
        """Update last_activity_at timestamp in Firestore."""
        self._db.collection(CONFIG_COLLECTION).document(CONFIG_DOCUMENT).update({
            "last_activity_at": datetime.now(timezone.utc).isoformat(),
        })
```

- [ ] **Step 1.4: Run tests to verify they pass**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-blue-green-encoding-deploy && python -m pytest backend/tests/test_encoding_worker_manager.py -v`
Expected: PASS

- [ ] **Step 1.5: Write tests for update_activity and swap_roles**

```python
# Add to backend/tests/test_encoding_worker_manager.py

    def test_update_activity_sets_timestamp(self):
        """Should update last_activity_at in Firestore."""
        self.manager.update_activity()

        self.mock_db.collection.return_value.document.return_value.update.assert_called_once()
        call_args = self.mock_db.collection.return_value.document.return_value.update.call_args[0][0]
        assert "last_activity_at" in call_args

    def test_swap_roles_swaps_primary_and_secondary(self):
        """Should atomically swap primary and secondary in Firestore."""
        mock_transaction = MagicMock()
        self.mock_db.transaction.return_value.__enter__ = MagicMock(return_value=mock_transaction)
        self.mock_db.transaction.return_value.__exit__ = MagicMock(return_value=False)

        self.manager.swap_roles(
            new_primary_vm="encoding-worker-b",
            new_primary_ip="35.4.5.6",
            new_primary_version="0.153.0",
            old_primary_vm="encoding-worker-a",
            old_primary_ip="34.1.2.3",
            old_primary_version="0.152.0",
        )

        # Verify transaction was used
        self.mock_db.transaction.assert_called_once()
```

- [ ] **Step 1.6: Implement swap_roles with Firestore transaction**

Add to `EncodingWorkerManager`:

```python
    def swap_roles(
        self,
        new_primary_vm: str,
        new_primary_ip: str,
        new_primary_version: str,
        old_primary_vm: str,
        old_primary_ip: str,
        old_primary_version: str,
    ) -> None:
        """Atomically swap primary and secondary roles in Firestore."""
        doc_ref = self._db.collection(CONFIG_COLLECTION).document(CONFIG_DOCUMENT)
        now = datetime.now(timezone.utc).isoformat()

        @firestore.transactional
        def swap_in_transaction(transaction):
            transaction.update(doc_ref, {
                "primary_vm": new_primary_vm,
                "primary_ip": new_primary_ip,
                "primary_version": new_primary_version,
                "primary_deployed_at": now,
                "secondary_vm": old_primary_vm,
                "secondary_ip": old_primary_ip,
                "secondary_version": old_primary_version,
                "secondary_deployed_at": now,
                "last_swap_at": now,
                "deploy_in_progress": False,
                "deploy_in_progress_since": None,
            })

        transaction = self._db.transaction()
        swap_in_transaction(transaction)
```

- [ ] **Step 1.7: Run all tests**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-blue-green-encoding-deploy && python -m pytest backend/tests/test_encoding_worker_manager.py -v`
Expected: PASS

- [ ] **Step 1.8: Commit**

```bash
git add backend/services/encoding_worker_manager.py backend/tests/test_encoding_worker_manager.py
git commit -m "feat: add encoding worker manager service with Firestore config"
```

---

## Task 2: VM Start/Stop via Compute API

**Files:**
- Modify: `backend/services/encoding_worker_manager.py`
- Modify: `backend/tests/test_encoding_worker_manager.py`

Add the ability to start/stop VMs and check their status via the GCE Compute API.

- [ ] **Step 2.1: Write failing tests for VM lifecycle methods**

```python
# Add to backend/tests/test_encoding_worker_manager.py
from unittest.mock import patch, MagicMock


class TestVMLifecycle:
    """Tests for VM start/stop operations."""

    def setup_method(self):
        self.mock_db = MagicMock()
        self.mock_compute = MagicMock()
        self.manager = EncodingWorkerManager(
            db=self.mock_db,
            compute_client=self.mock_compute,
            project_id="nomadkaraoke",
            zone="us-central1-c",
        )

    def test_get_vm_status_returns_status(self):
        """Should return VM status string from Compute API."""
        self.mock_compute.get.return_value.status = "TERMINATED"
        status = self.manager.get_vm_status("encoding-worker-a")
        assert status == "TERMINATED"
        self.mock_compute.get.assert_called_once_with(
            project="nomadkaraoke",
            zone="us-central1-c",
            instance="encoding-worker-a",
        )

    def test_start_vm_calls_compute_start(self):
        """Should call compute.start for the given VM."""
        self.mock_compute.get.return_value.status = "TERMINATED"
        self.manager.start_vm("encoding-worker-a")
        self.mock_compute.start.assert_called_once_with(
            project="nomadkaraoke",
            zone="us-central1-c",
            instance="encoding-worker-a",
        )

    def test_start_vm_skips_if_already_running(self):
        """Should not call start if VM is already RUNNING."""
        self.mock_compute.get.return_value.status = "RUNNING"
        self.manager.start_vm("encoding-worker-a")
        self.mock_compute.start.assert_not_called()

    def test_stop_vm_calls_compute_stop(self):
        """Should call compute.stop for the given VM."""
        self.mock_compute.get.return_value.status = "RUNNING"
        self.manager.stop_vm("encoding-worker-a")
        self.mock_compute.stop.assert_called_once_with(
            project="nomadkaraoke",
            zone="us-central1-c",
            instance="encoding-worker-a",
        )

    def test_stop_vm_skips_if_already_terminated(self):
        """Should not call stop if VM is already TERMINATED."""
        self.mock_compute.get.return_value.status = "TERMINATED"
        self.manager.stop_vm("encoding-worker-a")
        self.mock_compute.stop.assert_not_called()
```

- [ ] **Step 2.2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_encoding_worker_manager.py::TestVMLifecycle -v`
Expected: FAIL

- [ ] **Step 2.3: Implement VM lifecycle methods**

Add to `EncodingWorkerManager.__init__`:

```python
    def __init__(
        self,
        db: firestore.Client,
        compute_client=None,
        project_id: str = "nomadkaraoke",
        zone: str = "us-central1-c",
    ):
        self._db = db
        self._compute = compute_client
        self._project_id = project_id
        self._zone = zone
```

Add methods:

```python
    def get_vm_status(self, vm_name: str) -> str:
        """Get the current status of a VM (RUNNING, TERMINATED, STAGING, etc.)."""
        instance = self._compute.get(
            project=self._project_id,
            zone=self._zone,
            instance=vm_name,
        )
        return instance.status

    def start_vm(self, vm_name: str) -> None:
        """Start a VM if it's not already running."""
        status = self.get_vm_status(vm_name)
        if status == "RUNNING":
            return
        self._compute.start(
            project=self._project_id,
            zone=self._zone,
            instance=vm_name,
        )

    def stop_vm(self, vm_name: str) -> None:
        """Stop a VM if it's running."""
        status = self.get_vm_status(vm_name)
        if status == "TERMINATED":
            return
        self._compute.stop(
            project=self._project_id,
            zone=self._zone,
            instance=vm_name,
        )
```

- [ ] **Step 2.4: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_encoding_worker_manager.py -v`
Expected: PASS

- [ ] **Step 2.5: Write test for ensure_primary_running (warmup orchestration)**

```python
    def test_ensure_primary_running_starts_stopped_vm(self):
        """Should start the primary VM and update activity if it's stopped."""
        # Mock get_config to return config with primary VM
        self.mock_db.collection.return_value.document.return_value.get.return_value.exists = True
        self.mock_db.collection.return_value.document.return_value.get.return_value.to_dict.return_value = {
            "primary_vm": "encoding-worker-a",
            "primary_ip": "34.1.2.3",
            "secondary_vm": "encoding-worker-b",
            "secondary_ip": "35.4.5.6",
            "deploy_in_progress": False,
        }
        self.mock_compute.get.return_value.status = "TERMINATED"

        result = self.manager.ensure_primary_running()

        assert result["started"] is True
        assert result["vm_name"] == "encoding-worker-a"
        self.mock_compute.start.assert_called_once()
```

- [ ] **Step 2.6: Implement ensure_primary_running**

```python
    def ensure_primary_running(self) -> dict:
        """Ensure the primary VM is running. Start it if stopped.

        Returns dict with:
            started: bool - whether the VM was started (vs already running)
            vm_name: str - name of the primary VM
            primary_url: str - URL of the primary VM
        """
        config = self.get_config()
        status = self.get_vm_status(config.primary_vm)
        started = False

        if status != "RUNNING":
            self.start_vm(config.primary_vm)
            started = True

        self.update_activity()

        return {
            "started": started,
            "vm_name": config.primary_vm,
            "primary_url": config.primary_url,
        }
```

- [ ] **Step 2.7: Run all tests**

Run: `python -m pytest backend/tests/test_encoding_worker_manager.py -v`
Expected: PASS

- [ ] **Step 2.8: Commit**

```bash
git add backend/services/encoding_worker_manager.py backend/tests/test_encoding_worker_manager.py
git commit -m "feat: add VM lifecycle management via Compute API"
```

---

## Task 3: Backend Warmup + Heartbeat API Endpoints

**Files:**
- Create: `backend/api/routes/encoding_worker.py`
- Create: `backend/tests/test_encoding_worker_routes.py`
- Modify: `backend/api/app.py` (register router)

- [ ] **Step 3.1: Write failing tests for warmup endpoint**

```python
# backend/tests/test_encoding_worker_routes.py
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from fastapi.testclient import TestClient


class TestWarmupEndpoint:
    """Tests for POST /api/internal/encoding-worker/warmup."""

    def setup_method(self):
        # Import after patching to avoid loading real dependencies
        self.mock_manager = MagicMock()
        self.mock_manager.ensure_primary_running.return_value = {
            "started": True,
            "vm_name": "encoding-worker-a",
            "primary_url": "http://34.1.2.3:8080",
        }

    def test_warmup_returns_status(self):
        """Should return warmup result with started flag."""
        with patch("backend.api.routes.encoding_worker.get_worker_manager", return_value=self.mock_manager):
            from backend.api.routes.encoding_worker import router
            from fastapi import FastAPI
            app = FastAPI()
            app.include_router(router, prefix="/api/internal")

            client = TestClient(app)
            response = client.post(
                "/api/internal/encoding-worker/warmup",
                headers={"X-Admin-Token": "test-token"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["started"] is True
            assert data["vm_name"] == "encoding-worker-a"


class TestHeartbeatEndpoint:
    """Tests for POST /api/internal/encoding-worker/heartbeat."""

    def test_heartbeat_updates_activity(self):
        """Should update activity timestamp and return ok."""
        mock_manager = MagicMock()
        with patch("backend.api.routes.encoding_worker.get_worker_manager", return_value=mock_manager):
            from backend.api.routes.encoding_worker import router
            from fastapi import FastAPI
            app = FastAPI()
            app.include_router(router, prefix="/api/internal")

            client = TestClient(app)
            response = client.post(
                "/api/internal/encoding-worker/heartbeat",
                headers={"X-Admin-Token": "test-token"},
            )

            assert response.status_code == 200
            mock_manager.update_activity.assert_called_once()
```

- [ ] **Step 3.2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_encoding_worker_routes.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3.3: Implement the routes**

```python
# backend/api/routes/encoding_worker.py
"""
Encoding worker lifecycle endpoints.

Provides warmup and heartbeat endpoints for the blue-green
encoding worker VMs. Called by the frontend to ensure the
primary VM is running before encoding requests.
"""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends

from backend.api.dependencies import require_admin
from backend.services.encoding_worker_manager import EncodingWorkerManager

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/encoding-worker",
    tags=["encoding-worker"],
)

# Lazy-loaded singleton
_manager: EncodingWorkerManager | None = None


def get_worker_manager() -> EncodingWorkerManager:
    """Get or create the encoding worker manager singleton."""
    global _manager
    if _manager is None:
        from google.cloud import compute_v1, firestore
        from backend.config import get_settings

        settings = get_settings()
        db = firestore.Client(project=settings.google_cloud_project)
        compute_client = compute_v1.InstancesClient()
        _manager = EncodingWorkerManager(
            db=db,
            compute_client=compute_client,
            project_id=settings.google_cloud_project,
        )
    return _manager


@router.post("/warmup")
async def warmup_encoding_worker(
    background_tasks: BackgroundTasks,
    _admin: tuple = Depends(require_admin),
    manager: EncodingWorkerManager = Depends(get_worker_manager),
):
    """Start the primary encoding worker VM if it's stopped.

    Called by the frontend when a user opens the lyrics review page.
    Fire-and-forget: returns immediately, VM starts in background.
    """
    try:
        result = manager.ensure_primary_running()
        if result["started"]:
            logger.info(f"Started encoding worker VM: {result['vm_name']}")
        return result
    except Exception as e:
        logger.error(f"Failed to warm up encoding worker: {e}")
        return {"started": False, "error": str(e)}


@router.post("/heartbeat")
async def heartbeat_encoding_worker(
    _admin: tuple = Depends(require_admin),
    manager: EncodingWorkerManager = Depends(get_worker_manager),
):
    """Update activity timestamp to prevent idle shutdown.

    Called by the frontend on lyrics review interactions
    (autosave, preview click, etc.). Debounced to at most once per 60s.
    """
    try:
        manager.update_activity()
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Failed to update encoding worker heartbeat: {e}")
        return {"status": "error", "error": str(e)}
```

- [ ] **Step 3.4: Register the router in main.py**

Find the existing internal router registration in `backend/main.py` and add the encoding worker router alongside it:

```python
from backend.api.routes.encoding_worker import router as encoding_worker_router

# Add alongside existing internal router
app.include_router(encoding_worker_router, prefix="/api/internal")
```

- [ ] **Step 3.5: Run tests to verify they pass**

Run: `python -m pytest backend/tests/test_encoding_worker_routes.py -v`
Expected: PASS

- [ ] **Step 3.6: Commit**

```bash
git add backend/api/routes/encoding_worker.py backend/tests/test_encoding_worker_routes.py backend/main.py
git commit -m "feat: add warmup and heartbeat API endpoints for encoding worker"
```

---

## Task 4: Refactor EncodingService for Dynamic URL Resolution

**Files:**
- Modify: `backend/services/encoding_service.py`
- Modify: `backend/tests/test_encoding_service.py`

The current `EncodingService` caches the worker URL at init time. It must now read the primary IP from Firestore on each request (with a short TTL cache).

- [ ] **Step 4.1: Write failing test for dynamic URL resolution**

```python
# Add to backend/tests/test_encoding_service.py

class TestDynamicURLResolution:
    """Tests that EncodingService reads URL from Firestore, not static config."""

    def test_url_refreshes_from_firestore(self):
        """Should read primary_url from worker manager, not cached value."""
        mock_manager = MagicMock()
        mock_manager.get_config.return_value = MagicMock(
            primary_url="http://34.1.2.3:8080",
            primary_vm="encoding-worker-a",
        )

        service = EncodingService(
            api_key="test-key",
            worker_manager=mock_manager,
        )

        url = service._get_worker_url()
        assert url == "http://34.1.2.3:8080"
        mock_manager.get_config.assert_called_once()

    def test_url_caches_for_ttl(self):
        """Should cache URL and not re-read within TTL window."""
        mock_manager = MagicMock()
        mock_manager.get_config.return_value = MagicMock(
            primary_url="http://34.1.2.3:8080",
        )

        service = EncodingService(
            api_key="test-key",
            worker_manager=mock_manager,
        )

        # Call twice rapidly
        service._get_worker_url()
        service._get_worker_url()

        # Should only read from Firestore once (cached)
        assert mock_manager.get_config.call_count == 1
```

- [ ] **Step 4.2: Run tests to verify they fail**

Run: `python -m pytest backend/tests/test_encoding_service.py::TestDynamicURLResolution -v`
Expected: FAIL

- [ ] **Step 4.3: Refactor EncodingService**

Key changes to `backend/services/encoding_service.py`:

1. Add `worker_manager` parameter to `__init__` (optional, for backward compat during migration)
2. Add `_get_worker_url()` method with 30-second TTL cache
3. Replace all `self._url` references in `submit_encoding_job`, `get_job_status`, etc. with `self._get_worker_url()`
4. Add warmup-aware retry: if connection fails, try `ensure_primary_running()` then retry

```python
import time

# Key changes to __init__ — keep backward compat with existing constructor:
def __init__(self):
    self.settings = get_settings()
    self._api_key = None
    self._url = None
    self._worker_manager = None
    self._cached_url = None
    self._url_cached_at = 0
    self._URL_CACHE_TTL = 30  # seconds
    self._initialized = False

def _load_credentials(self):
    """Load API key from settings (existing pattern)."""
    # ... existing credential loading code stays the same ...
    self._api_key = self.settings.encoding_worker_api_key
    self._initialized = True

def set_worker_manager(self, manager):
    """Set the worker manager for dynamic URL resolution from Firestore."""
    self._worker_manager = manager

def _get_worker_url(self) -> str:
    """Get the current primary worker URL from Firestore (with TTL cache).

    Falls back to static URL from config if worker_manager is not set
    (backward compat during migration).
    """
    now = time.time()
    if self._cached_url and (now - self._url_cached_at) < self._URL_CACHE_TTL:
        return self._cached_url

    if self._worker_manager:
        config = self._worker_manager.get_config()
        self._cached_url = config.primary_url
        self._url_cached_at = now
        return self._cached_url

    # Fallback to static URL from config/Secret Manager
    if not self._initialized:
        self._load_credentials()
    return self._url
```

Also update the singleton factory `get_encoding_service()` at the bottom of the file:

```python
_encoding_service = None

def get_encoding_service() -> EncodingService:
    global _encoding_service
    if _encoding_service is None:
        _encoding_service = EncodingService()
        # Wire up worker manager for dynamic URL resolution
        try:
            from backend.services.encoding_worker_manager import EncodingWorkerManager
            from google.cloud import compute_v1, firestore
            settings = get_settings()
            db = firestore.Client(project=settings.google_cloud_project)
            compute_client = compute_v1.InstancesClient()
            manager = EncodingWorkerManager(
                db=db,
                compute_client=compute_client,
                project_id=settings.google_cloud_project,
            )
            _encoding_service.set_worker_manager(manager)
        except Exception:
            # Fallback to static URL if worker manager setup fails
            pass
    return _encoding_service
```

- [ ] **Step 4.4: Add warmup-aware retry logic**

Wrap the existing `submit_encoding_job` with connection-failure handling:

```python
async def _ensure_worker_available(self) -> str:
    """Ensure the encoding worker is running and return its URL.

    If the worker is unreachable, attempt to start it and wait for health.
    Returns the worker URL.
    """
    url = self._get_worker_url()
    try:
        # Quick health check
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{url}/health", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    return url
    except (aiohttp.ClientError, asyncio.TimeoutError):
        pass

    # Worker is down - try to start it
    if self._worker_manager:
        logger.info("Encoding worker unreachable, attempting to start VM...")
        result = self._worker_manager.ensure_primary_running()
        if result["started"]:
            # Wait for service to come online (poll health)
            url = result["primary_url"]
            for attempt in range(18):  # 18 * 5s = 90s max
                await asyncio.sleep(5)
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(
                            f"{url}/health",
                            timeout=aiohttp.ClientTimeout(total=5),
                        ) as resp:
                            if resp.status == 200:
                                logger.info(f"Encoding worker is online: {url}")
                                return url
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    continue
            raise RuntimeError("Encoding worker failed to start within 90 seconds")

    return url
```

- [ ] **Step 4.5: Run all encoding service tests**

Run: `python -m pytest backend/tests/test_encoding_service.py -v`
Expected: PASS (both old and new tests)

- [ ] **Step 4.6: Commit**

```bash
git add backend/services/encoding_service.py backend/tests/test_encoding_service.py
git commit -m "feat: dynamic URL resolution from Firestore with warmup-aware retry"
```

---

## Task 5: Pulumi Infrastructure — Two VMs

**Files:**
- Modify: `infrastructure/compute/encoding_worker_vm.py`
- Modify: `infrastructure/config.py`
- Modify: `infrastructure/__main__.py`
- Modify: `infrastructure/modules/iam/worker_sas.py`

- [ ] **Step 5.1: Add EncodingWorkerConfig to infrastructure/config.py**

```python
# Add to infrastructure/config.py

class EncodingWorkerConfig:
    """Configuration for blue-green encoding worker VMs."""
    VM_NAMES = ["encoding-worker-a", "encoding-worker-b"]
    IP_NAMES = ["encoding-worker-ip-a", "encoding-worker-ip-b"]
    IDLE_CHECK_SCHEDULE = "*/5 * * * *"  # Every 5 minutes
    IDLE_TIMEOUT_MINUTES = 15
    FUNCTION_NAME = "encoding-worker-idle-shutdown"
    FUNCTION_MEMORY = "256M"
    FUNCTION_TIMEOUT = 120  # 2 minutes
```

- [ ] **Step 5.2: Refactor encoding_worker_vm.py for two VMs**

Replace the single-VM functions with functions that create a pair:

```python
# infrastructure/compute/encoding_worker_vm.py
"""
Encoding Worker VM resources — blue-green deployment pair.

Manages two identical VMs (a/b) for zero-downtime deployments.
Only one is active (primary) at a time; the other is stopped (secondary).
Both auto-shutdown when idle to minimize cost.

See docs/superpowers/specs/2026-03-24-blue-green-encoding-worker-design.md
"""

import pulumi
from pulumi_gcp import compute, serviceaccount

from config import REGION, ENCODING_WORKER_ZONE, PROJECT_ID, MachineTypes, DiskSizes, EncodingWorkerConfig
from .startup_scripts import read_script


def create_encoding_worker_ips() -> list[compute.Address]:
    """Create static IPs for both encoding worker VMs."""
    ips = []
    for name in EncodingWorkerConfig.IP_NAMES:
        ip = compute.Address(
            name,
            name=name,
            region=REGION,
            address_type="EXTERNAL",
            description=f"Static external IP for {name}",
        )
        ips.append(ip)
    return ips


def create_encoding_worker_vms(
    ips: list[compute.Address],
    service_account: serviceaccount.Account,
) -> list[compute.Instance]:
    """Create the blue-green encoding worker VM pair.

    Both VMs are identical except for name and IP. They use the same
    Packer-built custom image with Python 3.13, FFmpeg, and fonts.
    Both start in TERMINATED state to avoid costs; the backend starts
    the primary on demand.
    """
    startup_script = read_script("encoding_worker.sh")
    custom_image = f"projects/{PROJECT_ID}/global/images/family/encoding-worker"

    vms = []
    for vm_name, ip in zip(EncodingWorkerConfig.VM_NAMES, ips):
        vm = compute.Instance(
            vm_name,
            name=vm_name,
            machine_type=MachineTypes.ENCODING_WORKER,
            zone=ENCODING_WORKER_ZONE,
            boot_disk=compute.InstanceBootDiskArgs(
                initialize_params=compute.InstanceBootDiskInitializeParamsArgs(
                    image=custom_image,
                    size=DiskSizes.ENCODING_WORKER,
                    type="hyperdisk-balanced",
                ),
            ),
            network_interfaces=[compute.InstanceNetworkInterfaceArgs(
                network="default",
                access_configs=[compute.InstanceNetworkInterfaceAccessConfigArgs(
                    nat_ip=ip.address,
                )],
            )],
            service_account=compute.InstanceServiceAccountArgs(
                email=service_account.email,
                scopes=["cloud-platform"],
            ),
            metadata_startup_script=startup_script,
            tags=["encoding-worker"],
            allow_stopping_for_update=True,
            advanced_machine_features=compute.InstanceAdvancedMachineFeaturesArgs(
                threads_per_core=2,
            ),
        )
        vms.append(vm)
    return vms


def create_encoding_worker_firewall() -> compute.Firewall:
    """Create firewall rules for encoding worker VMs.

    Opens port 8080 for the HTTP API. Both VMs share the same
    firewall rule via the 'encoding-worker' network tag.
    """
    return compute.Firewall(
        "encoding-worker-firewall",
        name="encoding-worker-allow-http",
        network="default",
        allows=[
            compute.FirewallAllowArgs(protocol="tcp", ports=["8080"]),
        ],
        source_ranges=["0.0.0.0/0"],
        target_tags=["encoding-worker"],
        description="Allow HTTP access to encoding workers (auth required)",
    )
```

- [ ] **Step 5.3: Add compute.instances.start permission for backend SA**

Add to `infrastructure/modules/iam/worker_sas.py`:

```python
def grant_backend_compute_permissions(
    backend_sa: serviceaccount.Account,
) -> dict:
    """Grant the backend SA permission to start/get encoding worker VMs.

    Needed for on-demand VM warmup when users open lyrics review.
    Uses a custom role with minimal permissions scoped to the project.
    """
    # Custom role with just the permissions needed
    custom_role = gcp.projects.IAMCustomRole(
        "encoding-worker-lifecycle-role",
        role_id="encodingWorkerLifecycle",
        title="Encoding Worker Lifecycle",
        description="Start and check status of encoding worker VMs",
        permissions=[
            "compute.instances.get",
            "compute.instances.start",
        ],
        project=PROJECT_ID,
    )

    binding = gcp.projects.IAMMember(
        "backend-encoding-worker-lifecycle",
        project=PROJECT_ID,
        role=custom_role.name,
        member=backend_sa.email.apply(lambda email: f"serviceAccount:{email}"),
    )

    return {"custom_role": custom_role, "binding": binding}
```

- [ ] **Step 5.4: Update __main__.py to wire up new resources**

Replace the existing encoding worker section (lines 401-406) in `infrastructure/__main__.py`:

```python
# ==================== Compute VMs ====================

# Encoding Worker VMs (blue-green pair for zero-downtime deployments)
encoding_worker_ips = encoding_worker_vm.create_encoding_worker_ips()
encoding_worker_instances = encoding_worker_vm.create_encoding_worker_vms(
    encoding_worker_ips, encoding_worker_sa
)
encoding_worker_firewall = encoding_worker_vm.create_encoding_worker_firewall()

# Grant backend SA permission to start encoding worker VMs
backend_compute_perms = worker_sas.grant_backend_compute_permissions(backend_service_account)
```

Update the exports section (lines 527-531):

```python
# Encoding workers (blue-green pair)
pulumi.export("encoding_worker_a_ip", encoding_worker_ips[0].address)
pulumi.export("encoding_worker_b_ip", encoding_worker_ips[1].address)
pulumi.export("encoding_worker_a_url", encoding_worker_ips[0].address.apply(lambda ip: f"http://{ip}:8080"))
pulumi.export("encoding_worker_b_url", encoding_worker_ips[1].address.apply(lambda ip: f"http://{ip}:8080"))
pulumi.export("encoding_worker_service_account", encoding_worker_sa.email)
pulumi.export("encoding_worker_a_name", encoding_worker_instances[0].name)
pulumi.export("encoding_worker_b_name", encoding_worker_instances[1].name)
```

- [ ] **Step 5.5: Validate Pulumi config**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-blue-green-encoding-deploy/infrastructure && pulumi preview --diff 2>&1 | tail -50`
Expected: Shows creation of 2 VMs, 2 IPs, deletion of old VM and IP, custom IAM role

- [ ] **Step 5.6: Commit**

```bash
git add infrastructure/compute/encoding_worker_vm.py infrastructure/config.py infrastructure/__main__.py infrastructure/modules/iam/worker_sas.py
git commit -m "feat: Pulumi infra for blue-green encoding worker VM pair"
```

---

## Task 6: Idle Auto-Shutdown Cloud Function

**Files:**
- Create: `infrastructure/functions/encoding_worker_idle/main.py`
- Create: `infrastructure/functions/encoding_worker_idle/requirements.txt`
- Create: `infrastructure/modules/encoding_worker_manager.py`
- Modify: `infrastructure/__main__.py`

- [ ] **Step 6.1: Create the Cloud Function**

```python
# infrastructure/functions/encoding_worker_idle/main.py
"""
Encoding Worker Idle Shutdown Cloud Function.

Triggered by Cloud Scheduler every 5 minutes. Checks both encoding worker
VMs (a/b) and stops any that are idle (no active jobs, no recent user
activity, no deploy in progress).

See docs/superpowers/specs/2026-03-24-blue-green-encoding-worker-design.md § Section 4

Environment variables:
- GCP_PROJECT: GCP project ID (default: nomadkaraoke)
- GCP_ZONE: Zone where VMs are located (default: us-central1-c)
- IDLE_TIMEOUT_MINUTES: Minutes of inactivity before shutdown (default: 15)
"""

import json
import logging
import os
from datetime import datetime, timezone, timedelta

import functions_framework
import requests
from flask import Request
from google.cloud import compute_v1, firestore

logger = logging.getLogger(__name__)

PROJECT_ID = os.environ.get("GCP_PROJECT", "nomadkaraoke")
ZONE = os.environ.get("GCP_ZONE", "us-central1-c")
IDLE_TIMEOUT_MINUTES = int(os.environ.get("IDLE_TIMEOUT_MINUTES", "15"))
DEPLOY_STALE_TIMEOUT_MINUTES = 20
ENCODING_WORKER_PORT = 8080

# Lazy-loaded clients
_compute_client = None
_firestore_client = None


def get_compute_client() -> compute_v1.InstancesClient:
    global _compute_client
    if _compute_client is None:
        _compute_client = compute_v1.InstancesClient()
    return _compute_client


def get_firestore_client() -> firestore.Client:
    global _firestore_client
    if _firestore_client is None:
        _firestore_client = firestore.Client(project=PROJECT_ID)
    return _firestore_client


def get_vm_status(vm_name: str) -> str:
    """Get VM status from Compute API."""
    client = get_compute_client()
    try:
        instance = client.get(project=PROJECT_ID, zone=ZONE, instance=vm_name)
        return instance.status
    except Exception as e:
        logger.error(f"Failed to get status for {vm_name}: {e}")
        return "UNKNOWN"


def get_vm_ip(vm_name: str) -> str | None:
    """Get the external IP of a VM."""
    client = get_compute_client()
    try:
        instance = client.get(project=PROJECT_ID, zone=ZONE, instance=vm_name)
        for iface in instance.network_interfaces:
            for access in iface.access_configs:
                if access.nat_i_p:
                    return access.nat_i_p
    except Exception:
        pass
    return None


def check_active_jobs(vm_ip: str) -> int:
    """Check active_jobs from the encoding worker health endpoint."""
    try:
        resp = requests.get(
            f"http://{vm_ip}:{ENCODING_WORKER_PORT}/health",
            timeout=5,
        )
        if resp.status_code == 200:
            return resp.json().get("active_jobs", 0)
    except Exception:
        pass
    return 0


def stop_vm(vm_name: str) -> None:
    """Stop a VM via Compute API."""
    client = get_compute_client()
    logger.info(f"Stopping idle VM: {vm_name}")
    client.stop(project=PROJECT_ID, zone=ZONE, instance=vm_name)


@functions_framework.http
def idle_shutdown(request: Request):
    """Check encoding worker VMs and stop idle ones.

    Called by Cloud Scheduler every 5 minutes.
    """
    now = datetime.now(timezone.utc)
    idle_cutoff = now - timedelta(minutes=IDLE_TIMEOUT_MINUTES)
    stale_cutoff = now - timedelta(minutes=DEPLOY_STALE_TIMEOUT_MINUTES)

    # Read Firestore config
    db = get_firestore_client()
    doc = db.collection("config").document("encoding-worker").get()
    if not doc.exists:
        logger.warning("Encoding worker config not found in Firestore")
        return json.dumps({"status": "no_config"}), 200

    config = doc.to_dict()

    # Check deploy_in_progress
    deploy_in_progress = config.get("deploy_in_progress", False)
    if deploy_in_progress:
        deploy_since = config.get("deploy_in_progress_since")
        if deploy_since:
            deploy_time = datetime.fromisoformat(deploy_since)
            if deploy_time < stale_cutoff:
                # Stale deploy flag — clear it
                logger.warning(
                    f"Clearing stale deploy_in_progress flag (started {deploy_since})"
                )
                db.collection("config").document("encoding-worker").update({
                    "deploy_in_progress": False,
                    "deploy_in_progress_since": None,
                })
                deploy_in_progress = False
            else:
                logger.info("Deploy in progress, skipping idle check")
                return json.dumps({"status": "deploy_in_progress"}), 200

    # Parse last_activity_at for per-VM checks below
    last_activity = config.get("last_activity_at")
    has_recent_activity = False
    if last_activity:
        activity_time = datetime.fromisoformat(last_activity)
        has_recent_activity = activity_time > idle_cutoff

    # Check each VM individually
    primary_vm = config.get("primary_vm")
    results = {}
    for vm_name in [primary_vm, config.get("secondary_vm")]:
        if not vm_name:
            continue

        status = get_vm_status(vm_name)
        if status != "RUNNING":
            results[vm_name] = f"already_{status.lower()}"
            continue

        # Check for active encoding jobs (applies to both primary and secondary)
        vm_ip = get_vm_ip(vm_name)
        if vm_ip:
            active_jobs = check_active_jobs(vm_ip)
            if active_jobs > 0:
                results[vm_name] = f"active_jobs={active_jobs}"
                logger.info(f"{vm_name} has {active_jobs} active jobs, keeping alive")
                continue

        # Recent user activity keeps the PRIMARY alive (not secondary)
        # Secondary should stop unless it has active jobs or a deploy is in progress
        if vm_name == primary_vm and has_recent_activity:
            results[vm_name] = "active_session"
            logger.info(f"{vm_name} (primary) kept alive due to recent activity")
            continue

        # VM is idle — stop it
        stop_vm(vm_name)
        results[vm_name] = "stopped"

    return json.dumps({"status": "checked", "results": results}), 200
```

- [ ] **Step 6.2: Write unit tests for the Cloud Function**

Create `infrastructure/functions/encoding_worker_idle/test_idle_shutdown.py`:

```python
import json
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta


class TestIdleShutdown:
    """Tests for the encoding worker idle shutdown Cloud Function."""

    def setup_method(self):
        self.mock_compute = MagicMock()
        self.mock_db = MagicMock()

    def _make_config(self, **overrides):
        config = {
            "primary_vm": "encoding-worker-a",
            "primary_ip": "34.1.2.3",
            "secondary_vm": "encoding-worker-b",
            "secondary_ip": "35.4.5.6",
            "deploy_in_progress": False,
            "deploy_in_progress_since": None,
            "last_activity_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        }
        config.update(overrides)
        return config

    @patch("main.get_firestore_client")
    @patch("main.get_compute_client")
    @patch("main.check_active_jobs", return_value=0)
    def test_stops_idle_running_vms(self, mock_jobs, mock_compute_fn, mock_db_fn):
        """Should stop VMs with no active jobs and no recent activity."""
        from main import idle_shutdown

        mock_db_fn.return_value = self.mock_db
        mock_compute_fn.return_value = self.mock_compute

        doc = MagicMock()
        doc.exists = True
        doc.to_dict.return_value = self._make_config()
        self.mock_db.collection.return_value.document.return_value.get.return_value = doc

        instance = MagicMock()
        instance.status = "RUNNING"
        iface = MagicMock()
        access = MagicMock()
        access.nat_i_p = "34.1.2.3"
        iface.access_configs = [access]
        instance.network_interfaces = [iface]
        self.mock_compute.get.return_value = instance

        request = MagicMock()
        response, code = idle_shutdown(request)
        result = json.loads(response)

        assert code == 200
        assert self.mock_compute.stop.call_count == 2  # Both VMs stopped

    @patch("main.get_firestore_client")
    @patch("main.get_compute_client")
    def test_skips_when_deploy_in_progress(self, mock_compute_fn, mock_db_fn):
        """Should skip idle check when deploy is in progress."""
        from main import idle_shutdown

        mock_db_fn.return_value = self.mock_db
        mock_compute_fn.return_value = self.mock_compute

        doc = MagicMock()
        doc.exists = True
        doc.to_dict.return_value = self._make_config(
            deploy_in_progress=True,
            deploy_in_progress_since=datetime.now(timezone.utc).isoformat(),
        )
        self.mock_db.collection.return_value.document.return_value.get.return_value = doc

        request = MagicMock()
        response, code = idle_shutdown(request)

        assert code == 200
        self.mock_compute.stop.assert_not_called()

    @patch("main.get_firestore_client")
    @patch("main.get_compute_client")
    @patch("main.check_active_jobs", return_value=0)
    def test_recent_activity_keeps_primary_alive_not_secondary(
        self, mock_jobs, mock_compute_fn, mock_db_fn
    ):
        """Recent activity should keep primary alive but still stop secondary."""
        from main import idle_shutdown

        mock_db_fn.return_value = self.mock_db
        mock_compute_fn.return_value = self.mock_compute

        doc = MagicMock()
        doc.exists = True
        doc.to_dict.return_value = self._make_config(
            last_activity_at=datetime.now(timezone.utc).isoformat(),  # recent
        )
        self.mock_db.collection.return_value.document.return_value.get.return_value = doc

        instance = MagicMock()
        instance.status = "RUNNING"
        iface = MagicMock()
        access = MagicMock()
        access.nat_i_p = "34.1.2.3"
        iface.access_configs = [access]
        instance.network_interfaces = [iface]
        self.mock_compute.get.return_value = instance

        request = MagicMock()
        response, code = idle_shutdown(request)
        result = json.loads(response)

        # Secondary should be stopped, primary should be kept alive
        assert result["results"]["encoding-worker-a"] == "active_session"
        assert result["results"]["encoding-worker-b"] == "stopped"
```

Run: `cd infrastructure/functions/encoding_worker_idle && python -m pytest test_idle_shutdown.py -v`

- [ ] **Step 6.3: Create requirements.txt**

```
functions-framework==3.*
google-cloud-compute==1.*
google-cloud-firestore==2.*
requests>=2.28.0
```

- [ ] **Step 6.4: Create Pulumi module for the Cloud Function**

```python
# infrastructure/modules/encoding_worker_manager.py
"""
Encoding Worker Idle Shutdown — Cloud Function + Cloud Scheduler.

Automatically stops encoding worker VMs after 15 minutes of inactivity.
Checks: active encoding jobs, recent user activity, deploy in progress.

See docs/superpowers/specs/2026-03-24-blue-green-encoding-worker-design.md § Section 4
"""

import base64
import json
from pathlib import Path

import pulumi
import pulumi_gcp as gcp
from config import PROJECT_ID, REGION, ENCODING_WORKER_ZONE, EncodingWorkerConfig


def create_idle_shutdown_service_account() -> gcp.serviceaccount.Account:
    """Create service account for the idle shutdown Cloud Function."""
    return gcp.serviceaccount.Account(
        "encoding-worker-idle-sa",
        account_id="encoding-worker-idle",
        display_name="Encoding Worker Idle Shutdown",
        description="Service account for the encoding worker idle auto-shutdown Cloud Function",
    )


def grant_idle_shutdown_permissions(
    service_account: gcp.serviceaccount.Account,
) -> dict:
    """Grant permissions to check and stop encoding worker VMs."""
    bindings = {}

    # Compute: get and stop instances
    bindings["compute_viewer"] = gcp.projects.IAMMember(
        "idle-shutdown-compute-viewer",
        project=PROJECT_ID,
        role="roles/compute.viewer",
        member=service_account.email.apply(lambda e: f"serviceAccount:{e}"),
    )

    # Custom role for stopping instances (viewer can't stop)
    stop_role = gcp.projects.IAMCustomRole(
        "encoding-worker-stop-role",
        role_id="encodingWorkerStop",
        title="Encoding Worker Stop",
        description="Stop encoding worker VMs (for idle shutdown)",
        permissions=["compute.instances.stop"],
        project=PROJECT_ID,
    )

    bindings["compute_stop"] = gcp.projects.IAMMember(
        "idle-shutdown-compute-stop",
        project=PROJECT_ID,
        role=stop_role.name,
        member=service_account.email.apply(lambda e: f"serviceAccount:{e}"),
    )

    # Firestore: read/write config document
    bindings["firestore"] = gcp.projects.IAMMember(
        "idle-shutdown-firestore",
        project=PROJECT_ID,
        role="roles/datastore.user",
        member=service_account.email.apply(lambda e: f"serviceAccount:{e}"),
    )

    return bindings


def create_idle_shutdown_resources() -> dict:
    """Create all resources for encoding worker idle auto-shutdown."""
    # Service account
    sa = create_idle_shutdown_service_account()
    perms = grant_idle_shutdown_permissions(sa)

    # Source code bucket
    source_bucket = gcp.storage.Bucket(
        "encoding-worker-idle-source",
        name=f"encoding-worker-idle-source-{PROJECT_ID}",
        location="US-CENTRAL1",
        force_destroy=True,
        uniform_bucket_level_access=True,
    )

    # Cloud Function (Gen2)
    function = gcp.cloudfunctionsv2.Function(
        "encoding-worker-idle-function",
        name=EncodingWorkerConfig.FUNCTION_NAME,
        location=REGION,
        description="Auto-stop idle encoding worker VMs",
        build_config=gcp.cloudfunctionsv2.FunctionBuildConfigArgs(
            runtime="python312",
            entry_point="idle_shutdown",
            source=gcp.cloudfunctionsv2.FunctionBuildConfigSourceArgs(
                storage_source=gcp.cloudfunctionsv2.FunctionBuildConfigSourceStorageSourceArgs(
                    bucket=source_bucket.name,
                    object="encoding-worker-idle-source.zip",
                ),
            ),
        ),
        service_config=gcp.cloudfunctionsv2.FunctionServiceConfigArgs(
            available_memory=EncodingWorkerConfig.FUNCTION_MEMORY,
            timeout_seconds=EncodingWorkerConfig.FUNCTION_TIMEOUT,
            min_instance_count=0,
            max_instance_count=1,
            service_account_email=sa.email,
            environment_variables={
                "GCP_PROJECT": PROJECT_ID,
                "GCP_ZONE": ENCODING_WORKER_ZONE,
                "IDLE_TIMEOUT_MINUTES": str(EncodingWorkerConfig.IDLE_TIMEOUT_MINUTES),
            },
        ),
    )

    # Allow Cloud Scheduler to invoke the function
    scheduler_invoker = gcp.cloudfunctionsv2.FunctionIamMember(
        "idle-shutdown-scheduler-invoker",
        project=PROJECT_ID,
        location=REGION,
        cloud_function=function.name,
        role="roles/cloudfunctions.invoker",
        member=sa.email.apply(lambda e: f"serviceAccount:{e}"),
    )

    # Gen2 functions also need Cloud Run invoker
    run_invoker = gcp.cloudrunv2.ServiceIamMember(
        "idle-shutdown-run-invoker",
        project=PROJECT_ID,
        location=REGION,
        name=function.name,
        role="roles/run.invoker",
        member=sa.email.apply(lambda e: f"serviceAccount:{e}"),
    )

    # Cloud Scheduler
    scheduler = gcp.cloudscheduler.Job(
        "encoding-worker-idle-scheduler",
        name="encoding-worker-idle-check",
        description="Check and stop idle encoding worker VMs",
        region=REGION,
        schedule=EncodingWorkerConfig.IDLE_CHECK_SCHEDULE,
        time_zone="UTC",
        http_target=gcp.cloudscheduler.JobHttpTargetArgs(
            uri=function.url,
            http_method="POST",
            oidc_token=gcp.cloudscheduler.JobHttpTargetOidcTokenArgs(
                service_account_email=sa.email,
            ),
        ),
        retry_config=gcp.cloudscheduler.JobRetryConfigArgs(
            retry_count=1,
            min_backoff_duration="60s",
            max_backoff_duration="300s",
        ),
    )

    return {
        "service_account": sa,
        "function": function,
        "scheduler": scheduler,
        "source_bucket": source_bucket,
    }
```

- [ ] **Step 6.5: Wire up in __main__.py**

Add after the encoding worker VMs section:

```python
from modules import encoding_worker_manager

# Encoding Worker Idle Shutdown (auto-stop after 15 min idle)
encoding_worker_idle_resources = encoding_worker_manager.create_idle_shutdown_resources()
```

Add exports:

```python
# Encoding worker idle shutdown
pulumi.export("encoding_worker_idle_function_url", encoding_worker_idle_resources["function"].url)
pulumi.export("encoding_worker_idle_scheduler", encoding_worker_idle_resources["scheduler"].name)
```

- [ ] **Step 6.6: Commit**

```bash
git add infrastructure/functions/encoding_worker_idle/ infrastructure/modules/encoding_worker_manager.py infrastructure/__main__.py
git commit -m "feat: idle auto-shutdown Cloud Function for encoding workers"
```

---

## Task 7: CI Blue-Green Deployment Workflow

**Files:**
- Modify: `.github/workflows/ci.yml`

This is the most critical task — replacing the current restart-and-verify flow with the full blue-green deploy sequence.

- [ ] **Step 7.1: Read the current deploy-encoding-worker job**

Read: `.github/workflows/ci.yml` lines 1020-1186 to understand the current structure.

- [ ] **Step 7.2: Rewrite the encoding worker deployment steps**

Replace the current deployment steps (drain → restart → verify) with the blue-green flow. The job should:

1. Upload wheel + startup.sh + version.txt to GCS (keep existing)
2. Read Firestore config to identify secondary VM
3. Check and wait for any concurrent deploy (`deploy_in_progress`)
4. Set `deploy_in_progress=true, deploy_in_progress_since=now`
5. Start secondary VM
6. Wait for health (poll `/health`, up to 3 min)
7. Verify `wheel_version` matches expected
8. Run deep health check: submit a test encode to secondary VM's IP directly
9. Wait for encode to complete, verify output
10. If FAIL: stop secondary, clear deploy flag, fail CI
11. If PASS: swap roles in Firestore (atomic)
12. Drain old primary if active jobs
13. Stop old primary

Key CI steps to add:

```yaml
      - name: Install Firestore client
        run: pip install google-cloud-firestore

      - name: Read Firestore config
        id: config
        run: |
          # Note: gcloud CLI doesn't support Firestore document reads.
          # Use Python with google-cloud-firestore instead.
          python3 << 'PYEOF'
          import json, os
          from google.cloud import firestore
          db = firestore.Client(project="nomadkaraoke")
          doc = db.collection("config").document("encoding-worker").get()
          if not doc.exists:
              print("::error::Encoding worker config not found in Firestore")
              raise SystemExit(1)
          data = doc.to_dict()
          with open(os.environ["GITHUB_OUTPUT"], "a") as f:
              for key in ["primary_vm", "primary_ip", "primary_version",
                          "secondary_vm", "secondary_ip"]:
                  f.write(f"{key}={data.get(key, '')}\n")
              f.write(f"deploy_in_progress={str(data.get('deploy_in_progress', False)).lower()}\n")
          print(f"Config: primary={data['primary_vm']}, secondary={data['secondary_vm']}")
          PYEOF

      - name: Wait for concurrent deploy
        if: steps.config.outputs.deploy_in_progress == 'true'
        run: |
          echo "Another deploy is in progress, waiting..."
          python3 << 'PYEOF'
          import time
          from google.cloud import firestore
          db = firestore.Client(project="nomadkaraoke")
          for i in range(20):
              time.sleep(30)
              doc = db.collection("config").document("encoding-worker").get()
              if not doc.to_dict().get("deploy_in_progress", False):
                  print("Concurrent deploy finished, proceeding")
                  break
          else:
              print("::error::Timed out waiting for concurrent deploy (10 min)")
              raise SystemExit(1)
          PYEOF

      - name: Set deploy in progress
        run: |
          python3 << 'PYEOF'
          from datetime import datetime, timezone
          from google.cloud import firestore
          db = firestore.Client(project="nomadkaraoke")
          db.collection("config").document("encoding-worker").update({
              "deploy_in_progress": True,
              "deploy_in_progress_since": datetime.now(timezone.utc).isoformat(),
          })
          print("Set deploy_in_progress=true")
          PYEOF

      - name: Start secondary VM
        run: |
          SECONDARY_VM="${{ steps.config.outputs.secondary_vm }}"
          echo "Starting secondary VM: $SECONDARY_VM"
          gcloud compute instances start "$SECONDARY_VM" \
            --zone=us-central1-c --project=nomadkaraoke || true

          # Wait for VM to be RUNNING
          for i in $(seq 1 12); do
            STATUS=$(gcloud compute instances describe "$SECONDARY_VM" \
              --zone=us-central1-c --project=nomadkaraoke \
              --format='value(status)')
            echo "VM status: $STATUS (attempt $i/12)"
            if [ "$STATUS" = "RUNNING" ]; then
              break
            fi
            sleep 10
          done

      - name: Wait for service health
        id: health
        run: |
          SECONDARY_IP="${{ steps.config.outputs.secondary_ip }}"
          EXPECTED_VERSION="${{ steps.version.outputs.version }}"
          HEALTH_URL="http://${SECONDARY_IP}:8080/health"

          echo "Waiting for service at $HEALTH_URL..."
          for i in $(seq 1 18); do
            RESPONSE=$(curl -sf "$HEALTH_URL" 2>/dev/null || echo "")
            if [ -n "$RESPONSE" ]; then
              WHEEL_VERSION=$(echo "$RESPONSE" | jq -r '.wheel_version // empty')
              ACTIVE_JOBS=$(echo "$RESPONSE" | jq -r '.active_jobs // 0')
              echo "Health: version=$WHEEL_VERSION, active_jobs=$ACTIVE_JOBS"

              if [ "$WHEEL_VERSION" = "$EXPECTED_VERSION" ]; then
                echo "Version matches! Service is healthy."
                echo "healthy=true" >> $GITHUB_OUTPUT
                exit 0
              fi
            fi
            echo "Attempt $i/18 - waiting 10s..."
            sleep 10
          done

          echo "::error::Service did not become healthy within 3 minutes"
          echo "healthy=false" >> $GITHUB_OUTPUT
          exit 1

      - name: Deep health check — real encode test
        if: steps.health.outputs.healthy == 'true'
        id: encode-test
        run: |
          SECONDARY_IP="${{ steps.config.outputs.secondary_ip }}"
          API_KEY=$(gcloud secrets versions access latest \
            --secret=encoding-worker-api-key --project=nomadkaraoke)

          # Upload test ASS file to GCS (the encoding worker reads from GCS)
          echo "Uploading test assets to GCS..."
          gsutil cp infrastructure/encoding-worker/test-assets/test-preview.ass \
            gs://karaoke-gen-storage-nomadkaraoke/deploy-test/test-preview.ass

          # Submit encode-preview request directly to secondary VM
          # The /encode-preview endpoint accepts ass_gcs_path (a GCS path)
          echo "Submitting test encode to $SECONDARY_IP..."
          RESPONSE=$(curl -sf -X POST "http://${SECONDARY_IP}:8080/encode-preview" \
            -H "X-API-Key: $API_KEY" \
            -H "Content-Type: application/json" \
            -d '{
              "job_id": "deploy-health-check",
              "ass_gcs_path": "gs://karaoke-gen-storage-nomadkaraoke/deploy-test/test-preview.ass",
              "duration_seconds": 10
            }')

          echo "Encode response: $RESPONSE"
          STATUS=$(echo "$RESPONSE" | jq -r '.status // "unknown"')

          if [ "$STATUS" != "accepted" ] && [ "$STATUS" != "completed" ]; then
            echo "::error::Encode test failed with status: $STATUS"
            echo "passed=false" >> $GITHUB_OUTPUT
            exit 1
          fi

          # Poll for completion (max 60s for a 10-second preview)
          for i in $(seq 1 12); do
            sleep 5
            JOB_STATUS=$(curl -sf \
              "http://${SECONDARY_IP}:8080/status/deploy-health-check" \
              -H "X-API-Key: $API_KEY" | jq -r '.status // "unknown"')
            echo "Encode status: $JOB_STATUS (attempt $i/12)"
            if [ "$JOB_STATUS" = "completed" ]; then
              echo "Encode test passed!"
              echo "passed=true" >> $GITHUB_OUTPUT
              # Clean up test artifacts
              gsutil rm -f gs://karaoke-gen-storage-nomadkaraoke/deploy-test/** || true
              exit 0
            elif [ "$JOB_STATUS" = "failed" ]; then
              echo "::error::Encode test failed"
              echo "passed=false" >> $GITHUB_OUTPUT
              exit 1
            fi
          done

          echo "::error::Encode test timed out after 60s"
          echo "passed=false" >> $GITHUB_OUTPUT
          exit 1

      - name: Rollback on failure
        if: failure() && (steps.health.outputs.healthy == 'false' || steps.encode-test.outputs.passed == 'false')
        run: |
          SECONDARY_VM="${{ steps.config.outputs.secondary_vm }}"
          echo "::warning::Stopping secondary VM $SECONDARY_VM due to failed health check"
          gcloud compute instances stop "$SECONDARY_VM" \
            --zone=us-central1-c --project=nomadkaraoke || true

          # Clear deploy flag
          python3 << 'PYEOF'
          from google.cloud import firestore
          db = firestore.Client(project="nomadkaraoke")
          db.collection("config").document("encoding-worker").update({
              "deploy_in_progress": False,
              "deploy_in_progress_since": None,
          })
          print("Cleared deploy_in_progress flag")
          PYEOF

      - name: Swap primary in Firestore (atomic transaction)
        if: steps.encode-test.outputs.passed == 'true'
        env:
          NEW_PRIMARY: ${{ steps.config.outputs.secondary_vm }}
          NEW_PRIMARY_IP: ${{ steps.config.outputs.secondary_ip }}
          NEW_VERSION: ${{ steps.version.outputs.version }}
          OLD_PRIMARY: ${{ steps.config.outputs.primary_vm }}
          OLD_PRIMARY_IP: ${{ steps.config.outputs.primary_ip }}
          OLD_VERSION: ${{ steps.config.outputs.primary_version }}
        run: |
          echo "Swapping: $OLD_PRIMARY -> secondary, $NEW_PRIMARY -> primary"
          python3 << 'PYEOF'
          import os
          from datetime import datetime, timezone
          from google.cloud import firestore

          db = firestore.Client(project="nomadkaraoke")
          doc_ref = db.collection("config").document("encoding-worker")
          now = datetime.now(timezone.utc).isoformat()

          @firestore.transactional
          def swap(transaction):
              transaction.update(doc_ref, {
                  "primary_vm": os.environ["NEW_PRIMARY"],
                  "primary_ip": os.environ["NEW_PRIMARY_IP"],
                  "primary_version": os.environ["NEW_VERSION"],
                  "primary_deployed_at": now,
                  "secondary_vm": os.environ["OLD_PRIMARY"],
                  "secondary_ip": os.environ["OLD_PRIMARY_IP"],
                  "secondary_version": os.environ["OLD_VERSION"],
                  "secondary_deployed_at": now,
                  "last_swap_at": now,
                  "deploy_in_progress": False,
                  "deploy_in_progress_since": None,
              })

          transaction = db.transaction()
          swap(transaction)
          print(f"Swapped: {os.environ['NEW_PRIMARY']} is now primary")
          PYEOF

      - name: Drain and stop old primary
        if: steps.encode-test.outputs.passed == 'true'
        run: |
          OLD_PRIMARY="${{ steps.config.outputs.primary_vm }}"
          OLD_PRIMARY_IP="${{ steps.config.outputs.primary_ip }}"
          API_KEY=$(gcloud secrets versions access latest \
            --secret=encoding-worker-api-key --project=nomadkaraoke)

          # Check for active jobs on old primary
          ACTIVE_JOBS=$(curl -sf "http://${OLD_PRIMARY_IP}:8080/health" \
            | jq -r '.active_jobs // 0' 2>/dev/null || echo "0")

          if [ "$ACTIVE_JOBS" -gt 0 ]; then
            echo "Old primary has $ACTIVE_JOBS active jobs, waiting for drain..."
            DRAIN_TIMEOUT=600
            ELAPSED=0
            while [ $ELAPSED -lt $DRAIN_TIMEOUT ]; do
              sleep 15
              ELAPSED=$((ELAPSED + 15))
              ACTIVE_JOBS=$(curl -sf "http://${OLD_PRIMARY_IP}:8080/health" \
                | jq -r '.active_jobs // 0' 2>/dev/null || echo "0")
              echo "Active jobs: $ACTIVE_JOBS (${ELAPSED}s elapsed)"
              if [ "$ACTIVE_JOBS" -eq 0 ]; then
                break
              fi
            done
          fi

          echo "Stopping old primary: $OLD_PRIMARY"
          gcloud compute instances stop "$OLD_PRIMARY" \
            --zone=us-central1-c --project=nomadkaraoke || true
```

- [ ] **Step 7.3: Create test assets for deep health check**

```bash
mkdir -p infrastructure/encoding-worker/test-assets
```

Create `infrastructure/encoding-worker/test-assets/test-preview.ass` with a minimal valid ASS subtitle:

```ass
[Script Info]
Title: Deploy Health Check
ScriptType: v4.00+
WrapStyle: 0
PlayResX: 1920
PlayResY: 1080

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Noto Sans,60,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,3,0,2,20,20,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:05.00,Default,,0,0,0,,Health check test
Dialogue: 0,0:00:05.00,0:00:10.00,Default,,0,0,0,,Deploy verification
```

This is a minimal valid ASS file that produces two subtitle lines over 10 seconds — enough to verify FFmpeg, font rendering, and the full encoding pipeline work.

- [ ] **Step 7.4: Commit**

```bash
git add .github/workflows/ci.yml infrastructure/encoding-worker/test-assets/
git commit -m "feat: CI blue-green deploy with deep health check for encoding worker"
```

---

## Task 8: Frontend Warmup + Heartbeat Integration

**Files:**
- Modify: `frontend/lib/api.ts`
- Modify: `frontend/components/lyrics-review/LyricsAnalyzer.tsx`
- Modify: `frontend/components/lyrics-review/PreviewVideoSection.tsx`

- [ ] **Step 8.1: Add warmup and heartbeat API methods**

Add to `frontend/lib/api.ts` in the API client class:

```typescript
  /**
   * Signal the backend to start the encoding worker VM.
   * Fire-and-forget — doesn't wait for the VM to boot.
   */
  async warmupEncodingWorker(): Promise<void> {
    try {
      await fetch(`${this.baseUrl}/api/internal/encoding-worker/warmup`, {
        method: 'POST',
        headers: this.getAuthHeaders(),
      })
    } catch {
      // Fire-and-forget, don't throw on failure
    }
  }

  /**
   * Send heartbeat to keep encoding worker alive during active session.
   * Debounced externally — call at most once per 60 seconds.
   */
  async heartbeatEncodingWorker(): Promise<void> {
    try {
      await fetch(`${this.baseUrl}/api/internal/encoding-worker/heartbeat`, {
        method: 'POST',
        headers: this.getAuthHeaders(),
      })
    } catch {
      // Fire-and-forget
    }
  }
```

- [ ] **Step 8.2: Add warmup call on lyrics review page mount**

In `frontend/components/lyrics-review/LyricsAnalyzer.tsx`, add a `useEffect` near the top of the component (after the existing data loading effects):

```typescript
  // Warm up encoding worker VM when lyrics review page loads
  useEffect(() => {
    apiClient.warmupEncodingWorker()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Debounced heartbeat — keeps VM alive during active editing
  const lastHeartbeat = useRef(0)
  const sendHeartbeat = useCallback(() => {
    const now = Date.now()
    if (now - lastHeartbeat.current > 60_000) {
      lastHeartbeat.current = now
      apiClient.heartbeatEncodingWorker()
    }
  }, [apiClient])
```

Then call `sendHeartbeat()` in the existing autosave handler and wherever user interactions are tracked.

- [ ] **Step 8.3: Add "warming up" state to PreviewVideoSection**

In `frontend/components/lyrics-review/PreviewVideoSection.tsx`, handle the case where the encoding worker is starting:

```typescript
  // In the generatePreview function, handle worker_starting status
  const generatePreview = async () => {
    setStatus('loading')
    try {
      const response = await apiClient.generatePreviewVideo(jobId, correctionData)
      if (response.status === 'worker_starting') {
        setStatus('warming_up')
        // Poll for readiness, then retry
        // ...existing retry logic...
        return
      }
      // ...existing success handling...
    } catch (error) {
      setStatus('error')
    }
  }

  // Add warming up state in the JSX
  {status === 'warming_up' && (
    <div className="flex flex-col items-center justify-center p-8 text-center">
      <Loader2 className="h-8 w-8 animate-spin text-blue-500 mb-4" />
      <p className="text-sm text-gray-600">
        Starting encoding worker... This usually takes about a minute.
      </p>
    </div>
  )}
```

- [ ] **Step 8.4: Commit**

```bash
git add frontend/lib/api.ts frontend/components/lyrics-review/LyricsAnalyzer.tsx frontend/components/lyrics-review/PreviewVideoSection.tsx
git commit -m "feat: frontend warmup signal and warming-up UI for encoding worker"
```

---

## Task 9: Seed Firestore Config + Backend Config Migration

**Files:**
- Modify: `backend/config.py`
- Create: `scripts/seed-encoding-worker-config.py`

- [ ] **Step 9.1: Create Firestore seeding script**

```python
# scripts/seed-encoding-worker-config.py
"""
Seed the Firestore config/encoding-worker document.

Run once during migration to initialize the blue-green config.
Uses the current encoding worker's static IP as VM A's IP.

Usage:
    python scripts/seed-encoding-worker-config.py

Requires:
    - GOOGLE_CLOUD_PROJECT=nomadkaraoke (or gcloud default project)
    - Firestore write access
"""

import sys
from datetime import datetime, timezone
from google.cloud import firestore

PROJECT_ID = "nomadkaraoke"


def get_current_ip():
    """Get the current encoding worker IP from Pulumi or gcloud."""
    import subprocess
    result = subprocess.run(
        ["gcloud", "compute", "addresses", "describe", "encoding-worker-ip-a",
         "--region=us-central1", "--project=nomadkaraoke", "--format=value(address)"],
        capture_output=True, text=True
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()

    # Fallback: try the old IP name
    result = subprocess.run(
        ["gcloud", "compute", "addresses", "describe", "encoding-worker-static-ip",
         "--region=us-central1", "--project=nomadkaraoke", "--format=value(address)"],
        capture_output=True, text=True
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()

    print("ERROR: Could not determine encoding worker IP")
    print("Please provide it as an argument: python seed-encoding-worker-config.py <ip-a> <ip-b>")
    sys.exit(1)


def main():
    if len(sys.argv) == 3:
        ip_a, ip_b = sys.argv[1], sys.argv[2]
    else:
        print("Usage: python seed-encoding-worker-config.py <ip-a> <ip-b>")
        print("  ip-a: Static IP assigned to encoding-worker-a")
        print("  ip-b: Static IP assigned to encoding-worker-b")
        sys.exit(1)

    db = firestore.Client(project=PROJECT_ID)
    now = datetime.now(timezone.utc).isoformat()

    config = {
        "primary_vm": "encoding-worker-a",
        "primary_ip": ip_a,
        "primary_version": "unknown",
        "primary_deployed_at": now,
        "secondary_vm": "encoding-worker-b",
        "secondary_ip": ip_b,
        "secondary_version": None,
        "secondary_deployed_at": None,
        "last_swap_at": None,
        "last_activity_at": now,
        "deploy_in_progress": False,
        "deploy_in_progress_since": None,
    }

    doc_ref = db.collection("config").document("encoding-worker")

    # Check if already exists
    if doc_ref.get().exists:
        print("WARNING: config/encoding-worker already exists!")
        print("Current value:", doc_ref.get().to_dict())
        response = input("Overwrite? (y/N): ")
        if response.lower() != "y":
            print("Aborted.")
            sys.exit(0)

    doc_ref.set(config)
    print(f"Seeded config/encoding-worker:")
    print(f"  Primary: encoding-worker-a ({ip_a})")
    print(f"  Secondary: encoding-worker-b ({ip_b})")
    print(f"  Timestamp: {now}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 9.2: Update backend config to remove static encoding_worker_url**

In `backend/config.py`, the `encoding_worker_url` field should remain for now (backward compat during migration) but add a note that it will be removed once the Firestore config is live.

- [ ] **Step 9.3: Commit**

```bash
git add scripts/seed-encoding-worker-config.py backend/config.py
git commit -m "feat: add Firestore config seeding script for blue-green encoding worker"
```

---

## Task 10: Documentation

**Files:**
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/DEVELOPMENT.md`
- Modify: `docs/TROUBLESHOOTING.md`
- Modify: `docs/LESSONS-LEARNED.md`

- [ ] **Step 10.1: Update ARCHITECTURE.md**

Add a section on encoding worker blue-green deployment:
- Architecture diagram (the ASCII art from the spec)
- Explanation of the Firestore config document
- Traffic routing flow
- Deployment sequence

- [ ] **Step 10.2: Update DEVELOPMENT.md**

Add instructions for:
- How to seed the Firestore config locally
- How to manually start/stop encoding worker VMs
- How to trigger a blue-green deploy manually
- How to roll back a deploy

- [ ] **Step 10.3: Update TROUBLESHOOTING.md**

Add runbook entries for:
- "Encoding worker not starting" — check Firestore config, VM status, IAM permissions
- "Deploy stuck in progress" — how to clear the stale flag
- "Both VMs running unexpectedly" — check Cloud Function logs, Firestore timestamps
- "Encoding requests failing after deploy" — check Firestore swap, URL cache TTL

- [ ] **Step 10.4: Update LESSONS-LEARNED.md**

Add entry about:
- Why blue-green was chosen over MIG rolling updates
- The deep health check pattern (real encode test, not just HTTP ping)
- JIT startup tied to user workflow
- Cost optimization via idle shutdown

- [ ] **Step 10.5: Commit**

```bash
git add docs/ARCHITECTURE.md docs/DEVELOPMENT.md docs/TROUBLESHOOTING.md docs/LESSONS-LEARNED.md
git commit -m "docs: add blue-green encoding worker architecture and runbooks"
```

---

## Task 11: Integration Test + Migration Execution

- [ ] **Step 11.1: Run all backend tests**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-blue-green-encoding-deploy && make test 2>&1 | tail -n 500`
Expected: All tests pass

- [ ] **Step 11.2: Run Pulumi preview**

Run: `cd infrastructure && pulumi preview --diff`
Expected: Shows expected resource changes (2 new VMs, 2 new IPs, old VM removed, new Cloud Function, etc.)

- [ ] **Step 11.3: Apply Pulumi locally (before merging)**

Per CLAUDE.md: always run `pulumi up` locally before merging to avoid partial state from spot VM CI.

Run: `cd infrastructure && pulumi up`

- [ ] **Step 11.4: Seed Firestore config**

After Pulumi creates the new IPs, run the seeding script with the assigned IPs:

```bash
# Get the assigned IPs from Pulumi output
IP_A=$(pulumi stack output encoding_worker_a_ip)
IP_B=$(pulumi stack output encoding_worker_b_ip)

python scripts/seed-encoding-worker-config.py "$IP_A" "$IP_B"
```

- [ ] **Step 11.5: Verify end-to-end**

1. Start VM A manually: `gcloud compute instances start encoding-worker-a --zone=us-central1-c`
2. Verify health: `curl http://$IP_A:8080/health`
3. Test warmup endpoint: `curl -X POST https://api.nomadkaraoke.com/api/internal/encoding-worker/warmup -H "X-Admin-Token: $TOKEN"`
4. Verify idle shutdown stops VM after 15 min (or trigger Cloud Scheduler manually)

- [ ] **Step 11.6: Commit any final fixes**

```bash
git add -A && git commit -m "fix: integration test fixes for blue-green encoding worker"
```
