"""
Height data routes: multi-source building height fetch + merge.

Endpoints:
  POST /api/height/sources  — list available providers for a bbox
  POST /api/height/fetch    — fetch + merge heights from providers
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.server.core.responses import error_response
from app.server.schemas import BoundingBox
from geo2stl.projections import project_grid as _project_grid

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
    providers: list[ProviderInfo]


class HeightFetchRequest(BoundingBox):
    """Fetch and merge building heights from specified providers."""
    width: int = Field(256, ge=1, le=4096)
    height: int = Field(256, ge=1, le=4096)
    providers: list[str] | None = Field(
        None,
        description="Provider names to use. None = all available."
    )
    projection: str = Field(
        "none",
        description="Map projection: 'none', 'cosine', 'mercator', 'sinusoidal'"
    )
    clip_valid_region: bool = Field(
        True,
        description="Clip NaN-only border rows/cols from projected output"
    )


# ── Provider registry ───────────────────────────────────────────

# The registry lives in `city2stl.height.service` and is imported, not
# restated. This router used to keep its own copy of the provider list and a
# `_PROVIDER_META` table beside it; the two drifted, and the copy here was the
# one that went stale. It was still advertising `lidar_3dep` at 0.95 / 1 m after
# bug 3 corrected it to 0.82 / 30 m, and it never learned about GlobalBuilding-
# Atlas, so `/api/height/sources` denied the existence of a provider that the
# export path was already using.
from city2stl.height.service import (  # noqa: E402
    _select_providers,
    provider_infos,
)

# ── Endpoints ────────────────────────────────────────────────────

@router.post("/sources", response_model=HeightSourcesResponse)
async def height_sources(req: HeightSourcesRequest):
    """List height providers and whether they cover the given bbox."""
    bbox = (req.north, req.south, req.east, req.west)
    return HeightSourcesResponse(
        providers=[ProviderInfo(**info) for info in provider_infos(bbox)]
    )


@router.post("/fetch")
async def height_fetch(req: HeightFetchRequest):
    """Fetch and merge building heights from multiple providers."""
    import numpy as np

    from city2stl.height import HeightResult, merge_height_rasters

    bbox = (req.north, req.south, req.east, req.west)
    dim = (req.height, req.width)

    # Select providers
    providers, unknown = _select_providers(bbox, req.providers)
    if unknown:
        logger.warning(f"Unknown providers ignored: {unknown}")

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
            req.projection, req.clip_valid_region, categorical=False,
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
