"""POST /api/parse-karaoke-titles — batch karaoke-filename → artist/title.

Internal admin endpoint used by kjbox to canonicalise downloaded-file names.
Reuses the Vertex Gemini parser; never blocks the caller (kjbox degrades to a
deterministic guess when this is unavailable).
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.api.dependencies import require_admin
from backend.services.auth_service import AuthResult
from backend.services.parse_titles import parse_titles

logger = logging.getLogger(__name__)

router = APIRouter(tags=["parse-titles"])


class ParseItem(BaseModel):
    id: str
    filename: str
    channel: Optional[str] = None
    source: Optional[str] = None


class ParseRequest(BaseModel):
    items: list[ParseItem]


class ParseResult(BaseModel):
    id: str
    artist: str
    title: str
    confidence: float


class ParseResponse(BaseModel):
    results: list[ParseResult]


@router.post("/parse-karaoke-titles", response_model=ParseResponse)
async def parse_karaoke_titles(
    body: ParseRequest,
    auth_result: AuthResult = Depends(require_admin),
):
    items = [i.model_dump() for i in body.items]
    results = await parse_titles(items)
    logger.info("parse-karaoke-titles: %d items", len(items))
    return {"results": results}
