"""Authenticated tenant bulk-upload endpoints.

Separate from ``tenant.py`` (which is public config only): these require a
tenant session and gate on the ``bulk_upload`` feature flag.
"""
import asyncio
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from backend.api.dependencies import require_auth
from backend.middleware.tenant import get_tenant_config_from_request
from backend.services.auth_service import AuthResult
from backend.services.tenant_bulk import analyze_filenames
from backend.services.tenant_bulk.analyze import default_generate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tenant/bulk", tags=["tenant-bulk"])

# Mirror consumer Bulk Mode's cap to bound LLM cost and upload load.
MAX_FILENAMES = 100


class BulkAnalyzeRequest(BaseModel):
    filenames: List[str] = Field(
        ..., description="Filenames from the operator's folder pick (no paths needed)"
    )


@router.post("/analyze")
async def analyze_bulk_filenames(
    request: Request,
    body: BulkAnalyzeRequest,
    auth_result: AuthResult = Depends(require_auth),
):
    """Analyse a list of filenames into proposed Mixed/Instrumental job rows.

    Pure analysis: filenames only, no uploads, no audio, no state written.
    Returns ``{rows, unpaired, ignored}`` for an editable review table.
    """
    tenant_config = get_tenant_config_from_request(request)
    if not tenant_config:
        raise HTTPException(
            status_code=403, detail="Bulk upload is only available on tenant portals"
        )
    if not getattr(tenant_config.features, "bulk_upload", False):
        raise HTTPException(
            status_code=403, detail="Bulk upload is not enabled for this portal"
        )

    filenames = [f for f in (body.filenames or []) if f and f.strip()]
    if not filenames:
        raise HTTPException(status_code=400, detail="No filenames provided")
    if len(filenames) > MAX_FILENAMES:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files ({len(filenames)}). Please submit at most {MAX_FILENAMES} at a time.",
        )

    # The LLM pass is a blocking Vertex call; run it off the event loop. It only
    # fires for files the regex could not pair, and analyze_filenames falls back
    # to the regex result if the model errors — so this never hard-fails.
    def _run() -> dict:
        return analyze_filenames(filenames, generate=default_generate).to_dict()

    analysis = await asyncio.to_thread(_run)
    logger.info(
        "tenant-bulk analyze: tenant=%s files=%d rows=%d unpaired=%d ignored=%d",
        tenant_config.id,
        len(filenames),
        len(analysis["rows"]),
        len(analysis["unpaired"]),
        len(analysis["ignored"]),
    )
    return analysis
