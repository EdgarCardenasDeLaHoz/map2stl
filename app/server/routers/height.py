"""
Height data routes: multi-source building height fetch + merge.

Endpoints:
  POST /api/height/sources      — list available providers for a bbox
  POST /api/height/fetch        — fetch + merge heights from providers
  POST /api/height/diagnostics  — per-provider coverage/stats without merging
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from app.server.core.responses import error_response
from app.server.core.height.service import (
    fetch_height_diagnostics as _fetch_height_diagnostics,
    fetch_height_payload as _fetch_height_payload,
    provider_infos as _provider_infos,
)
from app.server.schemas import (
    BoundingBox,
    HeightDiagnosticsRequest,
    HeightDiagnosticsResponse,
    HeightFetchRequest,
    HeightFetchResponse,
    HeightSourcesResponse,
    ProviderDiagnostics,
    ProviderInfo,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/height", tags=["height"])


# ── Endpoints ────────────────────────────────────────────────────

@router.post("/sources", response_model=HeightSourcesResponse)
async def height_sources(req: BoundingBox):
    """List height providers and whether they cover the given bbox."""
    bbox = (req.north, req.south, req.east, req.west)
    providers = [ProviderInfo(**item) for item in _provider_infos(bbox)]
    return HeightSourcesResponse(providers=providers)


@router.post("/fetch")
async def height_fetch(req: HeightFetchRequest):
    """Fetch and merge building heights from multiple providers."""
    payload, error_message, status_code = await _fetch_height_payload(
        req.north, req.south, req.east, req.west,
        req.width, req.height,
        providers=req.providers,
        projection=req.projection,
        clip_nans=req.clip_nans,
    )
    if error_message is not None:
        return error_response(error_message, status_code)
    return payload


@router.post("/diagnostics", response_model=HeightDiagnosticsResponse)
async def height_diagnostics(req: HeightDiagnosticsRequest):
    """Fetch from each provider independently and return per-provider stats.

    Unlike /fetch, this does NOT merge results — useful for comparing coverage
    and quality across providers before choosing which to use.
    """
    data = await _fetch_height_diagnostics(
        req.north, req.south, req.east, req.west,
        req.width, req.height,
        providers=req.providers,
    )
    return HeightDiagnosticsResponse(
        providers=[ProviderDiagnostics(**item) for item in data["providers"]],
        errors=data["errors"],
    )
