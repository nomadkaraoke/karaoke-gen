"""
Encoding worker lifecycle endpoints.

Provides warmup and heartbeat endpoints for the blue-green
encoding worker VMs. Called by the frontend to ensure the
primary VM is running before encoding requests.
"""

import logging
from fastapi import APIRouter, Depends
from backend.api.dependencies import require_admin
from backend.services.encoding_worker_manager import EncodingWorkerManager

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


@router.post("/warmup")
async def warmup_encoding_worker(
    _admin=Depends(require_admin),
    manager=Depends(get_worker_manager),
):
    """Start the primary encoding worker VM if it's stopped."""
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
    _admin=Depends(require_admin),
    manager=Depends(get_worker_manager),
):
    """Update activity timestamp to prevent idle shutdown."""
    try:
        manager.update_activity()
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Failed to update encoding worker heartbeat: {e}")
        return {"status": "error", "error": str(e)}
