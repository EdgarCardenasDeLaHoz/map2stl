"""
Height data routes: multi-source building height fetch + merge.

Endpoints:
  POST /api/height/sources  — list available providers for a bbox
  POST /api/height/fetch    — fetch + merge heights from providers
"""

from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from geo2stl.projections import project_grid as _project_grid
from app.server.core.responses import error_response
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


# ── Provider registry ───────────────────────────────────────────

from city2stl.height.providers.ndsm import NDSMProvider
from city2stl.height.providers.wsf3d import WSF3DProvider
from city2stl.height.providers.copernicus import CopernicusProvider
from city2stl.height.providers.lidar_3dep import LiDAR3DEPProvider
from city2stl.height.providers.ghsl import GHSLProvider
from city2stl.height.providers.open_buildings import OpenBuildingsProvider
from city2stl.height.providers.shadow_height import ShadowHeightProvider

# Ordered by priority (highest confidence first)
_ALL_PROVIDERS = [
    LiDAR3DEPProvider(),      # 0.95 — US only, sub-metre
    NDSMProvider(),            # 0.80 — global, 30m
    CopernicusProvider(),      # 0.70 — EU only, 10m
    OpenBuildingsProvider(),   # 0.60 — developing regions, per-building
    WSF3DProvider(),           # 0.50 — global, 90m
    GHSLProvider(),            # 0.40 — global, 100m
    ShadowHeightProvider(),    # 0.30 — global, placeholder
]

_PROVIDER_MAP = {p.name: p for p in _ALL_PROVIDERS}

_PROVIDER_META = {
    "lidar_3dep":    {"confidence": 0.95, "resolution_m": 1.0},
    "ndsm":          {"confidence": 0.80, "resolution_m": 30.0},
    "copernicus":    {"confidence": 0.70, "resolution_m": 10.0},
    "open_buildings": {"confidence": 0.60, "resolution_m": 5.0},
    "wsf3d":          {"confidence": 0.50, "resolution_m": 90.0},
    "ghsl":           {"confidence": 0.40, "resolution_m": 100.0},
    "shadow_height":  {"confidence": 0.30, "resolution_m": 5.0},
}


# ── Endpoints ────────────────────────────────────────────────────

@router.post("/sources", response_model=HeightSourcesResponse)
async def height_sources(req: HeightSourcesRequest):
    """List height providers and whether they cover the given bbox."""
    bbox = (req.north, req.south, req.east, req.west)
    providers = []
    for p in _ALL_PROVIDERS:
        meta = _PROVIDER_META.get(p.name, {})
        providers.append(ProviderInfo(
            name=p.name,
            covers=p.covers(bbox),
            confidence=meta.get("confidence", 0.5),
            resolution_m=meta.get("resolution_m", 100.0),
        ))
    return HeightSourcesResponse(providers=providers)


@router.post("/fetch")
async def height_fetch(req: HeightFetchRequest):
    """Fetch and merge building heights from multiple providers."""
    import numpy as np
    from app.server.core.height import HeightResult, merge_height_rasters

    bbox = (req.north, req.south, req.east, req.west)
    dim = (req.height, req.width)

    # Select providers
    if req.providers:
        providers = [_PROVIDER_MAP[n] for n in req.providers if n in _PROVIDER_MAP]
        unknown = [n for n in req.providers if n not in _PROVIDER_MAP]
        if unknown:
            logger.warning(f"Unknown providers ignored: {unknown}")
    else:
        providers = [p for p in _ALL_PROVIDERS if p.covers(bbox)]

    if not providers:
        return error_response("No height providers available for this bbox", 404)

    # Fetch from each provider (blocking I/O → run in executor)
    loop = asyncio.get_running_loop()
    results: list[HeightResult] = []
    errors: list[str] = []

    for p in providers:
        try:
            hr = await loop.run_in_executor(
                None, p.fetch_heights, bbox, dim
            )
            results.append(hr)
            logger.info(f"Height provider '{p.name}': fetched "
                        f"{np.count_nonzero(~np.isnan(hr.raster))} valid pixels")
        except Exception as e:
            logger.warning(f"Height provider '{p.name}' failed: {e}")
            errors.append(f"{p.name}: {str(e)}")

    if not results:
        return error_response(
            f"All providers failed: {'; '.join(errors)}", 502
        )

    # Merge
    merged = merge_height_rasters(results, target_shape=dim)

    # Apply projection (same pattern as terrain endpoints)
    raster = merged.raster
    if req.projection != "none":
        raster = _project_grid(
            raster, req.north, req.south, req.east, req.west,
            req.projection, req.clip_nans, categorical=False,
        )

    # Stats (computed on the post-projection raster)
    valid = ~np.isnan(raster)
    total = raster.size
    n_valid = int(np.count_nonzero(valid))
    out_h, out_w = raster.shape
    coverage = n_valid / total * 100 if total > 0 else 0

    stats = {
        "providers_used": [r.source_name for r in results],
        "providers_failed": errors,
        "min_m": float(np.nanmin(raster)) if n_valid > 0 else None,
        "max_m": float(np.nanmax(raster)) if n_valid > 0 else None,
        "mean_m": float(np.nanmean(raster)) if n_valid > 0 else None,
        "valid_pixels": n_valid,
        "total_pixels": total,
    }

    # Encode raster as base64 for transport
    import base64
    raster_bytes = raster.tobytes()
    raster_b64 = base64.b64encode(raster_bytes).decode("ascii")

    return {
        "width": out_w,
        "height": out_h,
        "source_name": merged.source_name,
        "resolution_m": merged.resolution_m,
        "coverage_pct": round(coverage, 1),
        "stats": stats,
        "raster_b64": raster_b64,
        "dtype": "float32",
        "projection": req.projection,
    }
