# Service Health Status Modal — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static footer version line with a clickable element that opens a modal showing all service health, versions, deploy info, and (for admins) blue-green encoder details with PR links.

**Architecture:** New `/api/health/system-status` backend endpoint aggregates all service health + Firestore blue-green config into one response. Frontend gets a new `SystemStatusModal` component using the existing shadcn Dialog. Build metadata (git SHA, PR number/title) is baked in at CI time via env vars.

**Tech Stack:** FastAPI, Firestore, Next.js, shadcn Dialog, Tailwind, GitHub Actions

**Spec:** `docs/superpowers/specs/2026-03-31-service-health-modal-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|---------------|
| Modify | `backend/version.py` | Add build metadata (commit SHA, PR number/title) |
| Modify | `backend/api/routes/health.py` | New `/api/health/system-status` endpoint |
| Create | `backend/tests/test_system_status_endpoint.py` | Unit tests for the new endpoint |
| Modify | `frontend/next.config.mjs` | Pass new env vars to frontend build |
| Modify | `frontend/lib/api.ts` | Add `SystemStatus` types and `getSystemStatus()` |
| Create | `frontend/components/system-status-modal.tsx` | The modal component |
| Modify | `frontend/components/version-footer.tsx` | Make clickable, open modal |
| Modify | `.github/workflows/ci.yml` | Capture git SHA + PR info, pass to builds |
| Modify | `backend/services/gce_encoding/main.py` | Add commit_sha to health response |

---

### Task 1: Backend — Add build metadata to version module

**Files:**
- Modify: `backend/version.py`
- Test: `backend/tests/test_system_status_endpoint.py` (created in Task 2)

- [ ] **Step 1: Add build metadata env var readers to version.py**

In `backend/version.py`, add after the existing `VERSION = get_version()` line:

```python
# Build metadata — set by CI during deployment
COMMIT_SHA = os.environ.get("COMMIT_SHA", "")
PR_NUMBER = os.environ.get("PR_NUMBER", "")
PR_TITLE = os.environ.get("PR_TITLE", "")
STARTUP_TIME = None  # Set on first import


def _get_startup_time() -> str:
    """Record when the backend process started (proxy for deploy time)."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


STARTUP_TIME = _get_startup_time()
```

- [ ] **Step 2: Commit**

```bash
git add backend/version.py
git commit -m "feat: add build metadata env vars to version module"
```

---

### Task 2: Backend — New /api/health/system-status endpoint

**Files:**
- Modify: `backend/api/routes/health.py`
- Create: `backend/tests/test_system_status_endpoint.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_system_status_endpoint.py`:

```python
"""Tests for the /api/health/system-status endpoint."""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


@pytest.fixture
def mock_encoding_status():
    return {
        "configured": True,
        "enabled": True,
        "available": True,
        "status": "ok",
        "active_jobs": 0,
        "queue_length": 0,
        "wheel_version": "0.155.3",
    }


@pytest.fixture
def mock_flacfetch_status():
    return {
        "configured": True,
        "available": True,
        "status": "ok",
        "version": "0.19.1",
    }


@pytest.fixture
def mock_separator_status():
    return {
        "available": True,
        "status": "ok",
        "version": "0.4.3",
    }


@pytest.fixture
def mock_encoding_worker_config():
    """Mock EncodingWorkerConfig dataclass."""
    config = MagicMock()
    config.primary_vm = "encoding-worker-b"
    config.primary_ip = "34.10.189.118"
    config.primary_version = "0.155.3"
    config.primary_deployed_at = "2026-03-31T18:10:00Z"
    config.secondary_vm = "encoding-worker-a"
    config.secondary_ip = "34.57.78.246"
    config.secondary_version = "0.155.2"
    config.secondary_deployed_at = "2026-03-29T14:00:00Z"
    config.last_swap_at = "2026-03-31T18:10:00Z"
    config.deploy_in_progress = False
    config.deploy_in_progress_since = None
    config.last_activity_at = None
    return config


class TestSystemStatusPublic:
    """Tests for unauthenticated /api/health/system-status."""

    @pytest.mark.asyncio
    async def test_returns_all_services(
        self, mock_encoding_status, mock_flacfetch_status, mock_separator_status
    ):
        from backend.api.routes.health import system_status

        with patch("backend.api.routes.health.check_encoding_worker_status", new_callable=AsyncMock, return_value=mock_encoding_status), \
             patch("backend.api.routes.health.check_flacfetch_service_status", new_callable=AsyncMock, return_value=mock_flacfetch_status), \
             patch("backend.api.routes.health.check_audio_separator_status", return_value=mock_separator_status), \
             patch("backend.api.routes.health.VERSION", "0.155.3"), \
             patch("backend.api.routes.health.COMMIT_SHA", "8e2a949a"), \
             patch("backend.api.routes.health.PR_NUMBER", "644"), \
             patch("backend.api.routes.health.PR_TITLE", "fix: blue-green deploy"), \
             patch("backend.api.routes.health.STARTUP_TIME", "2026-03-31T18:10:00Z"):

            result = await system_status(auth=None)

        assert "services" in result
        services = result["services"]

        # All 5 services present
        assert "frontend" in services
        assert "backend" in services
        assert "encoder" in services
        assert "flacfetch" in services
        assert "separator" in services

        # Backend has build metadata
        backend_svc = services["backend"]
        assert backend_svc["version"] == "0.155.3"
        assert backend_svc["commit_sha"] == "8e2a949a"
        assert backend_svc["pr_number"] == "644"

        # Encoder has status
        assert services["encoder"]["status"] == "ok"
        assert services["encoder"]["version"] == "0.155.3"

        # No admin_details without auth
        assert "admin_details" not in services["encoder"]

    @pytest.mark.asyncio
    async def test_offline_service(self, mock_flacfetch_status, mock_separator_status):
        from backend.api.routes.health import system_status

        offline_encoding = {
            "configured": True,
            "enabled": True,
            "available": False,
            "error": "Connection refused",
        }

        with patch("backend.api.routes.health.check_encoding_worker_status", new_callable=AsyncMock, return_value=offline_encoding), \
             patch("backend.api.routes.health.check_flacfetch_service_status", new_callable=AsyncMock, return_value=mock_flacfetch_status), \
             patch("backend.api.routes.health.check_audio_separator_status", return_value=mock_separator_status), \
             patch("backend.api.routes.health.VERSION", "0.155.3"), \
             patch("backend.api.routes.health.COMMIT_SHA", ""), \
             patch("backend.api.routes.health.PR_NUMBER", ""), \
             patch("backend.api.routes.health.PR_TITLE", ""), \
             patch("backend.api.routes.health.STARTUP_TIME", "2026-03-31T18:10:00Z"):

            result = await system_status(auth=None)

        assert result["services"]["encoder"]["status"] == "offline"


class TestSystemStatusAdmin:
    """Tests for admin-authenticated /api/health/system-status."""

    @pytest.mark.asyncio
    async def test_admin_gets_bluegreen_details(
        self, mock_encoding_status, mock_flacfetch_status, mock_separator_status, mock_encoding_worker_config
    ):
        from backend.api.routes.health import system_status
        from backend.services.auth_service import UserType

        mock_auth = ("token123", UserType.ADMIN, -1)
        mock_manager = MagicMock()
        mock_manager.get_config.return_value = mock_encoding_worker_config

        with patch("backend.api.routes.health.check_encoding_worker_status", new_callable=AsyncMock, return_value=mock_encoding_status), \
             patch("backend.api.routes.health.check_flacfetch_service_status", new_callable=AsyncMock, return_value=mock_flacfetch_status), \
             patch("backend.api.routes.health.check_audio_separator_status", return_value=mock_separator_status), \
             patch("backend.api.routes.health.VERSION", "0.155.3"), \
             patch("backend.api.routes.health.COMMIT_SHA", ""), \
             patch("backend.api.routes.health.PR_NUMBER", ""), \
             patch("backend.api.routes.health.PR_TITLE", ""), \
             patch("backend.api.routes.health.STARTUP_TIME", "2026-03-31T18:10:00Z"), \
             patch("backend.api.routes.health._get_encoding_worker_manager", return_value=mock_manager):

            result = await system_status(auth=mock_auth)

        encoder = result["services"]["encoder"]
        assert "admin_details" in encoder
        admin = encoder["admin_details"]
        assert admin["primary_vm"] == "encoding-worker-b"
        assert admin["secondary_vm"] == "encoding-worker-a"
        assert admin["last_swap_at"] == "2026-03-31T18:10:00Z"
        assert admin["deploy_in_progress"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-fix-bluegreen-deploy && python -m pytest backend/tests/test_system_status_endpoint.py -v`
Expected: FAIL — `system_status` function does not exist yet.

- [ ] **Step 3: Implement the endpoint**

Add these imports to the top of `backend/api/routes/health.py` (after existing imports):

```python
from backend.version import VERSION, COMMIT_SHA, PR_NUMBER, PR_TITLE, STARTUP_TIME
from backend.api.dependencies import optional_auth
from backend.services.auth_service import UserType
```

Remove the existing `from backend.version import VERSION` line (now covered by the expanded import).

Add a helper to get the encoding worker manager (for testability):

```python
def _get_encoding_worker_manager():
    """Get the EncodingWorkerManager instance. Separated for testability."""
    try:
        encoding_service = get_encoding_service()
        if hasattr(encoding_service, '_worker_manager') and encoding_service._worker_manager:
            return encoding_service._worker_manager
    except Exception:
        pass
    return None
```

Add the endpoint (before the existing `@router.get("/health/detailed")` function):

```python
@router.get("/health/system-status")
async def system_status(
    auth: Optional[Tuple] = Depends(optional_auth),
) -> Dict[str, Any]:
    """
    Aggregated system status for all services.

    Returns basic info for everyone. Admin users get additional
    blue-green deployment details for the encoder.
    """
    is_admin = auth is not None and len(auth) >= 2 and auth[1] == UserType.ADMIN

    # Fetch all health statuses concurrently
    import asyncio
    encoding_task = check_encoding_worker_status()
    flacfetch_task = check_flacfetch_service_status()
    encoding_status, flacfetch_status = await asyncio.gather(
        encoding_task, flacfetch_task
    )
    separator_status = check_audio_separator_status()

    # Build frontend service info (from env vars baked at build time — returned as-is)
    frontend_svc = {
        "status": "ok",
        "version": VERSION,  # Frontend and backend share the same version from pyproject.toml
    }

    # Build backend service info
    backend_svc = {
        "status": "ok",
        "version": VERSION,
        "deployed_at": STARTUP_TIME,
    }
    if COMMIT_SHA:
        backend_svc["commit_sha"] = COMMIT_SHA
    if PR_NUMBER:
        backend_svc["pr_number"] = PR_NUMBER
    if PR_TITLE:
        backend_svc["pr_title"] = PR_TITLE

    # Build encoder service info
    encoder_svc = {
        "status": "ok" if encoding_status.get("available") else "offline",
        "version": encoding_status.get("wheel_version"),
        "active_jobs": encoding_status.get("active_jobs", 0),
    }

    # Build flacfetch service info
    flacfetch_svc = {
        "status": "ok" if flacfetch_status.get("available") else "offline",
        "version": flacfetch_status.get("version"),
    }

    # Build separator service info
    separator_svc = {
        "status": "ok" if separator_status.get("available") else "offline",
        "version": separator_status.get("version"),
    }

    # Admin-only: blue-green encoder details from Firestore
    if is_admin:
        manager = _get_encoding_worker_manager()
        if manager:
            try:
                config = manager.get_config()
                encoder_svc["admin_details"] = {
                    "primary_vm": config.primary_vm,
                    "primary_ip": config.primary_ip,
                    "primary_version": config.primary_version,
                    "primary_deployed_at": config.primary_deployed_at,
                    "secondary_vm": config.secondary_vm,
                    "secondary_ip": config.secondary_ip,
                    "secondary_version": config.secondary_version,
                    "secondary_deployed_at": config.secondary_deployed_at,
                    "last_swap_at": config.last_swap_at,
                    "deploy_in_progress": config.deploy_in_progress,
                    "active_jobs": encoding_status.get("active_jobs", 0),
                    "queue_length": encoding_status.get("queue_length", 0),
                }
            except Exception as e:
                logger.warning(f"Failed to get blue-green config: {e}")

        # Add error details for offline services
        if not flacfetch_status.get("available"):
            flacfetch_svc["admin_details"] = {"error": flacfetch_status.get("error")}
        if not separator_status.get("available"):
            separator_svc["admin_details"] = {"error": separator_status.get("error")}
        if not encoding_status.get("available") and "admin_details" not in encoder_svc:
            encoder_svc["admin_details"] = {"error": encoding_status.get("error")}

    return {
        "services": {
            "frontend": frontend_svc,
            "backend": backend_svc,
            "encoder": encoder_svc,
            "flacfetch": flacfetch_svc,
            "separator": separator_svc,
        }
    }
```

Also add `Tuple` to the `typing` import at the top of the file (add to existing `from typing import Dict, Any, Optional` line):

```python
from typing import Dict, Any, Optional, Tuple
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-fix-bluegreen-deploy && python -m pytest backend/tests/test_system_status_endpoint.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/api/routes/health.py backend/tests/test_system_status_endpoint.py
git commit -m "feat: add /api/health/system-status endpoint with admin blue-green details"
```

---

### Task 3: Frontend — Add API types and fetch function

**Files:**
- Modify: `frontend/lib/api.ts`

- [ ] **Step 1: Add SystemStatus types**

Add after the existing `AudioSeparatorHealth` interface (around line 305):

```typescript
export interface ServiceStatus {
  status: string;  // 'ok' | 'offline'
  version?: string | null;
  deployed_at?: string | null;
  commit_sha?: string | null;
  pr_number?: string | null;
  pr_title?: string | null;
  active_jobs?: number;
  admin_details?: {
    // Encoder blue-green
    primary_vm?: string;
    primary_ip?: string;
    primary_version?: string;
    primary_deployed_at?: string;
    secondary_vm?: string;
    secondary_ip?: string;
    secondary_version?: string;
    secondary_deployed_at?: string;
    last_swap_at?: string;
    deploy_in_progress?: boolean;
    active_jobs?: number;
    queue_length?: number;
    // Error details for offline services
    error?: string | null;
  };
}

export interface SystemStatus {
  services: {
    frontend: ServiceStatus;
    backend: ServiceStatus;
    encoder: ServiceStatus;
    flacfetch: ServiceStatus;
    separator: ServiceStatus;
  };
}
```

- [ ] **Step 2: Add getSystemStatus method**

Add to the `api` object (after the existing `getAudioSeparatorHealth` method):

```typescript
  /**
   * Get aggregated system status for all services
   */
  async getSystemStatus(): Promise<SystemStatus> {
    const response = await fetch(`${API_BASE_URL}/api/health/system-status`, {
      headers: getAuthHeaders(),
    });
    return handleResponse(response);
  },
```

- [ ] **Step 3: Commit**

```bash
git add frontend/lib/api.ts
git commit -m "feat: add SystemStatus types and getSystemStatus API method"
```

---

### Task 4: Frontend — Create SystemStatusModal component

**Files:**
- Create: `frontend/components/system-status-modal.tsx`

- [ ] **Step 1: Create the modal component**

Create `frontend/components/system-status-modal.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { api, SystemStatus, ServiceStatus } from "@/lib/api";

interface SystemStatusModalProps {
  open: boolean;
  onClose: () => void;
}

function formatRelativeTime(isoString: string | null | undefined): string {
  if (!isoString) return "";
  const date = new Date(isoString);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHours = Math.floor(diffMin / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  const diffDays = Math.floor(diffHours / 24);
  return `${diffDays}d ago`;
}

function truncate(str: string, maxLen: number): string {
  if (str.length <= maxLen) return str;
  return str.slice(0, maxLen - 1) + "…";
}

function StatusDot({ status }: { status: string }) {
  const isOk = status === "ok";
  return (
    <span className={isOk ? "text-green-500" : "text-red-500"}>
      {isOk ? "●" : "○"}
    </span>
  );
}

function ServiceCard({
  name,
  service,
  children,
}: {
  name: string;
  service: ServiceStatus;
  children?: React.ReactNode;
}) {
  const isOk = service.status === "ok";
  return (
    <div
      className="rounded-lg border p-4"
      style={{
        borderColor: "var(--card-border)",
        backgroundColor: "var(--card-bg)",
      }}
    >
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          <StatusDot status={service.status} />
          <span className="font-medium text-sm">{name}</span>
        </div>
        <span className="text-xs opacity-60">
          {isOk ? "Healthy" : "Offline"}
        </span>
      </div>

      {service.version && (
        <div className="text-xs opacity-75 mt-1">v{service.version}</div>
      )}

      {service.deployed_at && (
        <div className="text-xs opacity-50 mt-0.5">
          Deployed {formatRelativeTime(service.deployed_at)}
        </div>
      )}

      {service.pr_number && (
        <div className="text-xs mt-1">
          <a
            href={`https://github.com/nomadkaraoke/karaoke-gen/pull/${service.pr_number}`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-amber-500 hover:underline"
          >
            #{service.pr_number}
          </a>
          {service.pr_title && (
            <span className="opacity-60">
              {" — "}
              {truncate(service.pr_title, 50)}
            </span>
          )}
        </div>
      )}

      {children}
    </div>
  );
}

function BlueGreenSection({ details }: { details: NonNullable<ServiceStatus["admin_details"]> }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="mt-3">
      <button
        onClick={() => setExpanded(!expanded)}
        className="text-xs text-amber-500 hover:text-amber-400 flex items-center gap-1"
      >
        <span>{expanded ? "▾" : "▸"}</span>
        Blue-Green Deployment
        {details.deploy_in_progress && (
          <span className="text-amber-400 animate-pulse ml-1">⚠ deploying...</span>
        )}
      </button>

      {expanded && (
        <div className="mt-2 grid grid-cols-2 gap-2">
          {/* Primary */}
          <div
            className="rounded p-2 text-xs border"
            style={{
              borderColor: "rgba(74,222,128,0.3)",
              backgroundColor: "rgba(74,222,128,0.05)",
            }}
          >
            <div className="text-green-500 text-[10px] font-semibold uppercase tracking-wider">
              Primary
            </div>
            <div className="mt-1 font-mono">{details.primary_vm}</div>
            <div className="opacity-60">v{details.primary_version}</div>
            {details.primary_deployed_at && (
              <div className="opacity-40">
                {formatRelativeTime(details.primary_deployed_at)}
              </div>
            )}
          </div>

          {/* Secondary */}
          <div
            className="rounded p-2 text-xs border"
            style={{
              borderColor: "var(--card-border)",
              backgroundColor: "rgba(255,255,255,0.02)",
            }}
          >
            <div className="text-[10px] font-semibold uppercase tracking-wider opacity-50">
              Secondary (stopped)
            </div>
            <div className="mt-1 font-mono">{details.secondary_vm}</div>
            {details.secondary_version && (
              <div className="opacity-60">v{details.secondary_version}</div>
            )}
          </div>

          {/* Metadata row */}
          <div className="col-span-2 text-[10px] opacity-40 mt-1">
            {details.last_swap_at && (
              <span>Last swap: {formatRelativeTime(details.last_swap_at)}</span>
            )}
            {details.active_jobs != null && details.active_jobs > 0 && (
              <span className="ml-3">Active jobs: {details.active_jobs}</span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export function SystemStatusModal({ open, onClose }: SystemStatusModalProps) {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;

    setLoading(true);
    setError(null);

    api
      .getSystemStatus()
      .then(setStatus)
      .catch((e) => setError(e.message || "Failed to fetch status"))
      .finally(() => setLoading(false));
  }, [open]);

  const frontendVersion = process.env.NEXT_PUBLIC_APP_VERSION || "dev";
  const frontendCommitSha = process.env.NEXT_PUBLIC_COMMIT_SHA || "";
  const frontendPrNumber = process.env.NEXT_PUBLIC_PR_NUMBER || "";
  const frontendPrTitle = process.env.NEXT_PUBLIC_PR_TITLE || "";
  const frontendBuildTime = process.env.NEXT_PUBLIC_BUILD_TIME || "";

  // Override frontend service with build-time values
  const services = status?.services;
  const frontendService: ServiceStatus = {
    status: "ok",
    version: frontendVersion,
    deployed_at: frontendBuildTime || undefined,
    commit_sha: frontendCommitSha || undefined,
    pr_number: frontendPrNumber || undefined,
    pr_title: frontendPrTitle || undefined,
  };

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <DialogContent className="sm:max-w-lg" style={{ backgroundColor: "var(--card-bg)", borderColor: "var(--card-border)" }}>
        <DialogHeader>
          <DialogTitle className="text-foreground">System Status</DialogTitle>
        </DialogHeader>

        {loading && (
          <div className="py-8 text-center text-sm opacity-60">Loading...</div>
        )}

        {error && (
          <div className="py-8 text-center text-sm text-red-500">{error}</div>
        )}

        {services && !loading && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-2">
            <ServiceCard name="Frontend" service={frontendService} />
            <ServiceCard name="Backend" service={services.backend} />
            <ServiceCard name="Encoder" service={services.encoder}>
              {services.encoder.admin_details &&
                services.encoder.admin_details.primary_vm && (
                  <BlueGreenSection details={services.encoder.admin_details} />
                )}
            </ServiceCard>
            <ServiceCard name="Flacfetch" service={services.flacfetch} />
            <ServiceCard name="Separator" service={services.separator} />
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/components/system-status-modal.tsx
git commit -m "feat: add SystemStatusModal component with blue-green details"
```

---

### Task 5: Frontend — Make footer clickable and wire up modal

**Files:**
- Modify: `frontend/components/version-footer.tsx`

- [ ] **Step 1: Update version-footer to open modal on click**

Replace the entire content of `frontend/components/version-footer.tsx` with:

```tsx
"use client";

import { useEffect, useState } from "react";
import { api, EncodingWorkerHealth, FlacfetchHealth, AudioSeparatorHealth } from "@/lib/api";
import { SystemStatusModal } from "./system-status-modal";

export function VersionFooter() {
  const [backendVersion, setBackendVersion] = useState<string | null>(null);
  const [encodingWorker, setEncodingWorker] = useState<EncodingWorkerHealth | null>(null);
  const [flacfetch, setFlacfetch] = useState<FlacfetchHealth | null>(null);
  const [audioSeparator, setAudioSeparator] = useState<AudioSeparatorHealth | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  // Frontend version is injected at build time from pyproject.toml
  const frontendVersion = process.env.NEXT_PUBLIC_APP_VERSION || "dev";

  useEffect(() => {
    const fetchBackendVersion = async () => {
      try {
        const info = await api.getBackendInfo();
        setBackendVersion(info.version);
      } catch (error) {
        console.error("Failed to fetch backend version:", error);
        setBackendVersion(null);
      }
    };

    const fetchEncodingWorkerHealth = async () => {
      try {
        const health = await api.getEncodingWorkerHealth();
        setEncodingWorker(health);
      } catch (error) {
        console.error("Failed to fetch encoding worker health:", error);
        setEncodingWorker(null);
      }
    };

    const fetchFlacfetchHealth = async () => {
      try {
        const health = await api.getFlacfetchHealth();
        setFlacfetch(health);
      } catch (error) {
        console.error("Failed to fetch flacfetch health:", error);
        setFlacfetch(null);
      }
    };

    const fetchAudioSeparatorHealth = async () => {
      try {
        const health = await api.getAudioSeparatorHealth();
        setAudioSeparator(health);
      } catch (error) {
        console.error("Failed to fetch audio separator health:", error);
        setAudioSeparator(null);
      }
    };

    fetchBackendVersion();
    fetchEncodingWorkerHealth();
    fetchFlacfetchHealth();
    fetchAudioSeparatorHealth();
  }, []);

  // Render encoding worker status indicator
  const renderEncodingWorkerStatus = () => {
    if (!encodingWorker) return null;
    if (encodingWorker.status === "not_configured") return null;

    const isHealthy = encodingWorker.available && encodingWorker.status === "ok";
    const statusColor = isHealthy ? "text-green-500" : "text-red-500";
    const statusDot = isHealthy ? "●" : "○";

    let statusText = isHealthy ? "Encoder" : "Encoder offline";
    if (isHealthy && encodingWorker.version) {
      statusText = `Encoder: v${encodingWorker.version}`;
    }
    if (isHealthy && encodingWorker.active_jobs && encodingWorker.active_jobs > 0) {
      statusText += ` (${encodingWorker.active_jobs} active)`;
    }

    return (
      <>
        {" | "}
        <span className={statusColor}>{statusDot}</span> {statusText}
      </>
    );
  };

  // Render flacfetch status indicator
  const renderFlacfetchStatus = () => {
    if (!flacfetch) return null;
    if (flacfetch.status === "not_configured") return null;

    const isHealthy = flacfetch.available && flacfetch.status === "ok";
    const statusColor = isHealthy ? "text-green-500" : "text-red-500";
    const statusDot = isHealthy ? "●" : "○";

    let statusText = isHealthy ? "Flacfetch" : "Flacfetch offline";
    if (isHealthy && flacfetch.version) {
      statusText = `Flacfetch: v${flacfetch.version}`;
    }

    return (
      <>
        {" | "}
        <span className={statusColor}>{statusDot}</span> {statusText}
      </>
    );
  };

  // Render audio separator status indicator
  const renderAudioSeparatorStatus = () => {
    if (!audioSeparator) return null;
    if (audioSeparator.status === "not_configured") return null;

    const isHealthy = audioSeparator.available && audioSeparator.status === "ok";
    const statusColor = isHealthy ? "text-green-500" : "text-red-500";
    const statusDot = isHealthy ? "●" : "○";

    let statusText = isHealthy ? "Separator" : "Separator offline";
    if (isHealthy && audioSeparator.version) {
      statusText = `Separator: v${audioSeparator.version}`;
    }

    return (
      <>
        {" | "}
        <span className={statusColor}>{statusDot}</span> {statusText}
      </>
    );
  };

  return (
    <>
      <footer className="border-t mt-12 py-6" style={{ borderColor: 'var(--card-border)' }}>
        <div className="px-4 text-center text-sm" style={{ color: 'var(--text-muted)' }}>
          <p>
            Powered by{" "}
            <a href="https://github.com/nomadkaraoke/karaoke-gen" className="text-amber-500 hover:underline">
              karaoke-gen
            </a>
          </p>
          <p
            className="mt-1 text-xs opacity-75 cursor-pointer hover:opacity-100 transition-opacity"
            onClick={() => setModalOpen(true)}
            title="Click for detailed system status"
          >
            Frontend: v{frontendVersion}
            {backendVersion && (
              <>
                {" | "}
                Backend: v{backendVersion}
              </>
            )}
            {renderEncodingWorkerStatus()}
            {renderFlacfetchStatus()}
            {renderAudioSeparatorStatus()}
          </p>
        </div>
      </footer>

      <SystemStatusModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
      />
    </>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/components/version-footer.tsx
git commit -m "feat: make footer version line clickable to open system status modal"
```

---

### Task 6: Frontend — Pass build metadata env vars in next.config.mjs

**Files:**
- Modify: `frontend/next.config.mjs`

- [ ] **Step 1: Add new env vars to next.config.mjs**

In `frontend/next.config.mjs`, add to the `env` object (after the `NEXT_PUBLIC_APP_VERSION` line):

```javascript
  env: {
    NEXT_PUBLIC_APP_VERSION: appVersion,
    NEXT_PUBLIC_COMMIT_SHA: process.env.NEXT_PUBLIC_COMMIT_SHA || '',
    NEXT_PUBLIC_PR_NUMBER: process.env.NEXT_PUBLIC_PR_NUMBER || '',
    NEXT_PUBLIC_PR_TITLE: process.env.NEXT_PUBLIC_PR_TITLE || '',
    NEXT_PUBLIC_BUILD_TIME: process.env.NEXT_PUBLIC_BUILD_TIME || new Date().toISOString(),
  },
```

- [ ] **Step 2: Commit**

```bash
git add frontend/next.config.mjs
git commit -m "feat: pass build metadata env vars (commit, PR, build time) to frontend"
```

---

### Task 7: CI — Capture git metadata and pass to builds

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Find the deploy job and add build metadata step**

In `.github/workflows/ci.yml`, in the deploy job (the one that runs on merge to main), add a step early in the job (after checkout and before any build/deploy steps) to capture git info:

```yaml
      - name: Capture build metadata
        id: build-meta
        run: |
          echo "sha=${GITHUB_SHA::8}" >> $GITHUB_OUTPUT
          # For squash-merge commits, PR number is in the commit message as (#NNN)
          PR_NUM=$(git log -1 --format=%s | grep -oP '\(#\K[0-9]+' | head -1)
          if [ -n "$PR_NUM" ]; then
            PR_TITLE=$(gh pr view "$PR_NUM" --json title -q .title 2>/dev/null || echo "")
          else
            PR_NUM=""
            PR_TITLE=""
          fi
          echo "pr_number=${PR_NUM}" >> $GITHUB_OUTPUT
          echo "pr_title=${PR_TITLE}" >> $GITHUB_OUTPUT
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

- [ ] **Step 2: Pass metadata to frontend build**

Find the frontend build/deploy step (Cloudflare Pages deploy) and add env vars:

```yaml
        env:
          NEXT_PUBLIC_COMMIT_SHA: ${{ steps.build-meta.outputs.sha }}
          NEXT_PUBLIC_PR_NUMBER: ${{ steps.build-meta.outputs.pr_number }}
          NEXT_PUBLIC_PR_TITLE: ${{ steps.build-meta.outputs.pr_title }}
          NEXT_PUBLIC_BUILD_TIME: ${{ github.event.head_commit.timestamp }}
```

- [ ] **Step 3: Pass metadata to Cloud Run deploy**

Find the Cloud Run deploy step and add env vars to the service:

```yaml
          # Add to the gcloud run deploy command's --set-env-vars flag:
          COMMIT_SHA=${{ steps.build-meta.outputs.sha }},\
          PR_NUMBER=${{ steps.build-meta.outputs.pr_number }},\
          PR_TITLE=${{ steps.build-meta.outputs.pr_title }}
```

Note: Find the exact `gcloud run deploy` command in the CI file and add these to its existing `--set-env-vars` flag. If it uses `--env-vars-file`, add them there instead. The exact integration depends on how the existing deploy step is structured — read the file and match the pattern.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: capture git SHA and PR info, pass to frontend and backend deploys"
```

---

### Task 8: Run all tests and verify

- [ ] **Step 1: Run backend tests**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-fix-bluegreen-deploy
python -m pytest backend/tests/test_system_status_endpoint.py -v
```

Expected: All tests pass.

- [ ] **Step 2: Run frontend build to verify no TypeScript errors**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-fix-bluegreen-deploy/frontend
npx next build 2>&1 | tail -20
```

Expected: Build succeeds without errors.

- [ ] **Step 3: Run full test suite**

```bash
cd /Users/andrew/Projects/nomadkaraoke/karaoke-gen-fix-bluegreen-deploy
make test 2>&1 | tail -500
```

Expected: All tests pass.

- [ ] **Step 4: Bump version**

```bash
# Bump patch version in pyproject.toml
# Current version visible in pyproject.toml — increment the patch number
```

- [ ] **Step 5: Final commit**

```bash
git add pyproject.toml
git commit -m "chore: bump version to <new_version>"
```
