"""
Height data routes: multi-source building height fetch + merge.

Endpoints:
  POST /api/height/sources      — list available providers for a bbox
  POST /api/height/fetch        — fetch + merge heights from providers
  POST /api/height/diagnostics  — per-provider coverage/stats without merging
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.server.core.responses import error_response
from app.server.core.height.service import (
    fetch_height_diagnostics as _fetch_height_diagnostics,
    fetch_height_payload as _fetch_height_payload,
    provider_infos as _provider_infos,
)
from app.server.schemas import BoundingBox

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/height", tags=["height"])


# ── Request / response models ───────────────────────────────────

class HeightSourcesRequest(BoundingBox):
    """Check which height providers cover this bbox."""
    pass


class ProviderInfo(BaseModel):
    name: str
    covers: bool
    confidence: float
    resolution_m: float


class HeightSourcesResponse(BaseModel):
    providers: List[ProviderInfo]


class HeightFetchRequest(BoundingBox):
    """Fetch and merge building heights from specified providers."""
    width: int = Field(256, ge=1, le=4096)
    height: int = Field(256, ge=1, le=4096)
    providers: Optional[List[str]] = Field(
        None,
        description="Provider names to use. None = all available."
    )
    projection: str = Field(
        "none",
        description="Map projection: 'none', 'cosine', 'mercator', 'sinusoidal'"
    )
    clip_nans: bool = Field(
        True,
        description="Clip NaN-only border rows/cols from projected output"
    )


class HeightFetchResponse(BaseModel):
    width: int
    height: int
    source_name: str
    resolution_m: float
    coverage_pct: float = Field(description="% of pixels with data (non-NaN)")
    stats: dict


class HeightDiagnosticsRequest(BoundingBox):
    """Run all providers and return per-provider stats (no merge)."""
    width: int = Field(256, ge=1, le=4096)
    height: int = Field(256, ge=1, le=4096)
    providers: Optional[List[str]] = Field(None)


class ProviderDiagnostics(BaseModel):
    source: str
    coverage_pct: float
    valid_pixels: int
    total_pixels: int
    min_m: Optional[float]
    max_m: Optional[float]
    mean_m: Optional[float]
    p95_m: Optional[float]
    resolution_m: float
    confidence: float
    outliers_removed: int


class HeightDiagnosticsResponse(BaseModel):
    providers: List[ProviderDiagnostics]
    errors: List[str]


# ── Endpoints ────────────────────────────────────────────────────

@router.post("/sources", response_model=HeightSourcesResponse)
async def height_sources(req: HeightSourcesRequest):
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
