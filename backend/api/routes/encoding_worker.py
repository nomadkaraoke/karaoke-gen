"""
Encoding worker lifecycle endpoints.

Provides warmup and heartbeat endpoints for the blue-green
encoding worker VMs. Called by the lyrics-review frontend to ensure the
primary VM is running before the reviewer previews a render.

Auth: these are gated on ``require_review_auth`` (review access to a specific
job), NOT ``require_admin``. The callers are ordinary customers on the
lyrics-review page — gating on admin makes every non-admin caller 403, which
silently defeats the JIT pre-warm. Scoping to the job being reviewed keeps the
abuse posture identical to the rest of the review surface: you can only warm
the shared encoding VM if you already have review access to a real job.
"""

import logging
from typing import Tuple
from fastapi import APIRouter, Depends
from backend.api.dependencies import require_review_auth
from backend.services.encoding_worker_manager import EncodingWorkerManager
from backend.services.encoding_errors import EncodingWorkerStartError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/encoding-worker", tags=["encoding-worker"])

# Lazy-loaded singleton
_manager = None


def get_worker_manager():
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


@router.post("/warmup/{job_id}")
async def warmup_encoding_worker(
    job_id: str,
    _auth: Tuple[str, str] = Depends(require_review_auth),
    manager=Depends(get_worker_manager),
):
    """Start the primary encoding worker VM if it's stopped.

    Called on lyrics-review page load so the VM is warm by the time the
    reviewer previews a render. ``job_id`` scopes auth to the job under review.
    """
    try:
        result = manager.ensure_primary_running()
        if result["started"]:
            logger.info(f"Started encoding worker VM: {result['vm_name']}")
        return result
    except EncodingWorkerStartError as e:
        # The primary VM couldn't start (capacity exhaustion or a transient
        # 503 SERVICE_UNAVAILABLE from the GCE backend). This is NOT fatal:
        # the encoding flow's own warmup (ensure_any_running) falls back to
        # the alternate-zone workers, so the job still completes. Log at
        # WARNING so it doesn't trip the production error monitor and page us
        # for self-healing capacity events.
        logger.warning(
            f"Primary encoding worker warmup failed ({e}); "
            f"encoding will fall back to an alternate-zone worker"
        )
        return {"started": False, "error": str(e)}
    except Exception as e:
        logger.error(f"Failed to warm up encoding worker: {e}")
        return {"started": False, "error": str(e)}


@router.post("/heartbeat/{job_id}")
async def heartbeat_encoding_worker(
    job_id: str,
    _auth: Tuple[str, str] = Depends(require_review_auth),
    manager=Depends(get_worker_manager),
):
    """Update activity timestamp to prevent idle shutdown.

    Called periodically while the reviewer works. ``job_id`` scopes auth to the
    job under review.
    """
    try:
        manager.update_activity()
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Failed to update encoding worker heartbeat: {e}")
        return {"status": "error", "error": str(e)}
