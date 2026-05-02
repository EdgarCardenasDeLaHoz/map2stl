"""
Height data routes: multi-source building height fetch + merge.

Endpoints:
  POST /api/height/sources      — list available providers for a bbox
  POST /api/height/fetch        — fetch + merge heights from providers
  POST /api/height/diagnostics  — per-provider coverage/stats without merging
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.server.core.projection import project_grid as _project_grid
from app.server.core.responses import error_response
from app.server.schemas import BoundingBox

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/height", tags=["height"])

# ── Per-provider raster cache ────────────────────────────────────────────
# Each provider's (bbox, dim) → HeightResult is cached as a .npz under
# cache/height/<provider>/<key>.npz. Keys also include a CACHE_VERSION
# constant so we can invalidate when the provider's algorithm changes.
_HEIGHT_CACHE_VERSION = "v1"

try:
    from app.server.core.cache import CACHE_ROOT as _CACHE_ROOT
    _CACHE_AVAILABLE = _CACHE_ROOT is not None
except Exception:
    _CACHE_ROOT = None
    _CACHE_AVAILABLE = False


def _provider_cache_key(name: str, bbox, dim) -> str:
    n, s, e, w = bbox
    h, wpx = dim
    return hashlib.md5(
        f"{_HEIGHT_CACHE_VERSION}|{name}|{n:.5f}_{s:.5f}_{e:.5f}_{w:.5f}|{h}x{wpx}".encode()
    ).hexdigest()


def _read_provider_cache(name: str, bbox, dim):
    """Return cached HeightResult or None."""
    if not _CACHE_AVAILABLE:
        return None
    try:
        import numpy as np
        from app.server.core.height import HeightResult
        path = _CACHE_ROOT / "height" / name / f"{_provider_cache_key(name, bbox, dim)}.npz"
        if not path.exists():
            return None
        arr = np.load(path)
        return HeightResult(
            raster=arr["raster"],
            confidence=arr["confidence"],
            source_name=str(arr["source_name"]),
            resolution_m=float(arr["resolution_m"]),
        )
    except Exception as e:
        logger.debug(f"height cache read failed for {name}: {e}")
        return None


def _write_provider_cache(name: str, bbox, dim, hr) -> None:
    if not _CACHE_AVAILABLE:
        return
    try:
        import numpy as np
        d = _CACHE_ROOT / "height" / name
        d.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            d / f"{_provider_cache_key(name, bbox, dim)}.npz",
            raster=hr.raster.astype(np.float32),
            confidence=hr.confidence.astype(np.float32),
            source_name=hr.source_name,
            resolution_m=hr.resolution_m,
        )
    except Exception as e:
        logger.debug(f"height cache write failed for {name}: {e}")


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


# ── Provider registry ───────────────────────────────────────────

from app.server.core.height.providers.ndsm import NDSMProvider
from app.server.core.height.providers.wsf3d import WSF3DProvider
from app.server.core.height.providers.copernicus import CopernicusProvider
from app.server.core.height.providers.lidar_3dep import LiDAR3DEPProvider
from app.server.core.height.providers.ghsl import GHSLProvider
from app.server.core.height.providers.open_buildings import OpenBuildingsProvider
from app.server.core.height.providers.shadow_height import ShadowHeightProvider
from app.server.core.height.providers.roofnet import RoofNetProvider


# Ordered by priority (highest confidence first)
_ALL_PROVIDERS = [
    LiDAR3DEPProvider(),      # 0.95 — US only, sub-metre
    NDSMProvider(),           # 0.80 — global, 30m
    CopernicusProvider(),     # 0.70 — EU only, 10m
    RoofNetProvider(),        # 0.65 — trained CNN on satellite RGB; global if checkpoint available
    OpenBuildingsProvider(),  # 0.60 — developing regions, per-building
    WSF3DProvider(),          # 0.50 — global, 90m
    GHSLProvider(),           # 0.40 — global, 100m
    # ShadowHeightProvider(), # 0.30 — DEPRECATED, do not use by default
]

_PROVIDER_MAP = {p.name: p for p in _ALL_PROVIDERS}

_PROVIDER_META = {
    "lidar_3dep":    {"confidence": 0.95, "resolution_m": 1.0},
    "ndsm":          {"confidence": 0.80, "resolution_m": 30.0},
    "copernicus":    {"confidence": 0.70, "resolution_m": 10.0},
    "roofnet":       {"confidence": 0.65, "resolution_m": 5.0},
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

    # Fetch from all providers in parallel (each does blocking I/O → run_in_executor)
    loop = asyncio.get_running_loop()
    results: list[HeightResult] = []
    errors: list[str] = []

    async def _fetch_one(p) -> HeightResult | None:
        cached = _read_provider_cache(p.name, bbox, dim)
        if cached is not None:
            logger.info(f"Height provider '{p.name}': cache hit")
            return cached
        try:
            hr = await loop.run_in_executor(None, p.fetch_heights, bbox, dim)
            logger.info(
                f"Height provider '{p.name}': fetched "
                f"{np.count_nonzero(~np.isnan(hr.raster))} valid pixels"
            )
            _write_provider_cache(p.name, bbox, dim, hr)
            return hr
        except Exception as e:
            logger.warning(f"Height provider '{p.name}' failed: {e}")
            errors.append(f"{p.name}: {str(e)}")
            return None

    gathered = await asyncio.gather(*[_fetch_one(p) for p in providers])
    results = [hr for hr in gathered if hr is not None]

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

    # Per-provider contribution: how much of the merged raster came from each source
    providers_contribution = []
    for r in results:
        meta = _PROVIDER_META.get(r.source_name, {})
        providers_contribution.append({
            "name": r.source_name,
            "resolution_m": r.resolution_m,
            "confidence": meta.get("confidence", 0.5),
        })

    vmin = float(np.nanmin(raster)) if n_valid > 0 else None
    vmax = float(np.nanmax(raster)) if n_valid > 0 else None
    stats = {
        "providers_used": [r.source_name for r in results],
        "providers_failed": errors,
        "providers": providers_contribution,
        "min_m": vmin,
        "max_m": vmax,
        "mean_m": float(np.nanmean(raster)) if n_valid > 0 else None,
        "valid_pixels": n_valid,
        "total_pixels": total,
    }

    # Encode raster as base64 for transport
    import base64
    raster_bytes = raster.tobytes()
    raster_b64 = base64.b64encode(raster_bytes).decode("ascii")

    # Final bbox in response — when projection clips NaN borders the output
    # bbox can differ slightly from the request bbox. We don't currently
    # compute the clipped bbox here, but reporting the input bbox is still
    # useful for clients that can't see the full request.
    return {
        "width": out_w,
        "height": out_h,
        "source_name": merged.source_name,
        "resolution_m": merged.resolution_m,
        "coverage_pct": round(coverage, 1),
        "stats": stats,
        "raster_b64": raster_b64,
        "dtype": "float32",
        "units": "metres",
        "projection": req.projection,
        "bbox": {"north": req.north, "south": req.south,
                 "east": req.east, "west": req.west},
        "vmin": vmin,
        "vmax": vmax,
    }


@router.post("/diagnostics", response_model=HeightDiagnosticsResponse)
async def height_diagnostics(req: HeightDiagnosticsRequest):
    """Fetch from each provider independently and return per-provider stats.

    Unlike /fetch, this does NOT merge results — useful for comparing coverage
    and quality across providers before choosing which to use.
    """
    import numpy as np
    from app.server.core.height import _filter_outliers, provider_stats

    bbox = (req.north, req.south, req.east, req.west)
    dim = (req.height, req.width)

    if req.providers:
        providers = [_PROVIDER_MAP[n] for n in req.providers if n in _PROVIDER_MAP]
        unknown = [n for n in req.providers if n not in _PROVIDER_MAP]
        if unknown:
            logger.warning(f"Unknown providers ignored: {unknown}")
    else:
        providers = [p for p in _ALL_PROVIDERS if p.covers(bbox)]

    if not providers:
        return HeightDiagnosticsResponse(
            providers=[],
            errors=["No height providers available for this bbox"],
        )

    loop = asyncio.get_running_loop()
    errors: list[str] = []

    async def _fetch_one(p):
        meta = _PROVIDER_META.get(p.name, {})
        cached = _read_provider_cache(p.name, bbox, dim)
        try:
            if cached is not None:
                hr = cached
                logger.info(f"Diagnostics: cache hit for '{p.name}'")
            else:
                hr = await loop.run_in_executor(None, p.fetch_heights, bbox, dim)
                _write_provider_cache(p.name, bbox, dim, hr)

            # Count pixels that survive outlier filtering
            raw_valid = int(np.count_nonzero(~np.isnan(hr.raster)))
            filtered = _filter_outliers(hr.raster)
            filtered_valid = int(np.count_nonzero(~np.isnan(filtered)))
            outliers_removed = raw_valid - filtered_valid

            # Compute stats on the filtered raster
            stats = provider_stats(hr.__class__(
                raster=filtered,
                confidence=hr.confidence,
                source_name=hr.source_name,
                resolution_m=hr.resolution_m,
            ))

            return ProviderDiagnostics(
                source=hr.source_name,
                coverage_pct=stats["coverage_pct"],
                valid_pixels=stats["valid_pixels"],
                total_pixels=stats["total_pixels"],
                min_m=stats["min_m"],
                max_m=stats["max_m"],
                mean_m=stats["mean_m"],
                p95_m=stats["p95_m"],
                resolution_m=hr.resolution_m,
                confidence=meta.get("confidence", 0.5),
                outliers_removed=outliers_removed,
            )
        except Exception as e:
            logger.warning(f"Height diagnostics provider '{p.name}' failed: {e}")
            errors.append(f"{p.name}: {str(e)}")
            return None

    gathered = await asyncio.gather(*[_fetch_one(p) for p in providers])
    provider_results = [r for r in gathered if r is not None]

    return HeightDiagnosticsResponse(providers=provider_results, errors=errors)
