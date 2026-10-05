"""
routers/cache.py — /api/cache/* endpoints.

Extracted from location_picker.py (backend refactor, step 6).
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.server.config import CACHE_CLEAR_INTERVAL, CACHE_DIRS, CACHE_MAX_FILES

logger = logging.getLogger(__name__)
router = APIRouter(tags=["cache"])

_last_cache_clear: float = 0.0


# ---------------------------------------------------------------------------
# Route helpers
# ---------------------------------------------------------------------------

async def _clear_cache():
    global _last_cache_clear
    cleared = []
    for cache_dir in CACHE_DIRS:
        if cache_dir.exists() and cache_dir.is_dir():
            cache_files = list(cache_dir.glob("*"))
            deleted = 0
            for f in cache_files:
                try:
                    f.unlink()
                    deleted += 1
                except Exception:
                    logger.debug('Could not delete cache file', exc_info=True)
            cleared.append({"path": str(
                cache_dir), "files_deleted": deleted, "total_files": len(cache_files)})
            logger.info(
                f"Cleared cache: {cache_dir} ({deleted}/{len(cache_files)} files)")
    _last_cache_clear = time.time()
    return JSONResponse(content={"status": "success", "cleared": cleared})


async def _get_cache_status():
    cache_info = []
    total_files = 0
    total_size = 0
    for cache_dir in CACHE_DIRS:
        if cache_dir.exists() and cache_dir.is_dir():
            cache_files = list(cache_dir.glob("*.jbl"))
            dir_size = sum(f.stat().st_size for f in cache_files if f.exists())
            total_files += len(cache_files)
            total_size += dir_size
            recent = sorted(
                cache_files, key=lambda f: f.stat().st_mtime, reverse=True)[:5]
            recent_info = [
                {"name": f.name, "size_kb": round(f.stat().st_size / 1024, 1),
                 "age_minutes": round((time.time() - f.stat().st_mtime) / 60, 1)}
                for f in recent
            ]
            cache_info.append({
                "path": str(cache_dir), "file_count": len(cache_files),
                "size_mb": round(dir_size / (1024 * 1024), 2), "recent_files": recent_info,
            })
    return JSONResponse(content={
        "status": "ok", "total_cached_files": total_files,
        "total_size_mb": round(total_size / (1024 * 1024), 2),
        "max_files": CACHE_MAX_FILES, "caches": cache_info,
        "last_clear": _last_cache_clear,
        "clear_interval_hours": CACHE_CLEAR_INTERVAL / 3600,
    })


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/api/cache")
async def get_cache_info():
    """Return cache status and file counts."""
    return await _get_cache_status()


@router.delete("/api/cache")
async def clear_cache_endpoint():
    """Clear all cached Earth Engine tiles."""
    return await _clear_cache()


@router.delete("/api/cache/region")
async def clear_region_cache(
    north: float = Query(..., description="Bounding box north"),
    south: float = Query(..., description="Bounding box south"),
    east: float = Query(..., description="Bounding box east"),
    west: float = Query(..., description="Bounding box west"),
):
    """Clear all namespace caches (DEM, water, satellite, etc.) for a region.

    Expects bbox query params: north, south, east, west.
    """
    from app.server.core.cache import clear_bbox_cache

    results = clear_bbox_cache(north, south, east, west)
    total = sum(results.values())
    return JSONResponse(content={
        "status": "success",
        "files_deleted": total,
        "by_namespace": results,
    })


@router.get("/api/cache/check")
async def check_cache(
    north: float = Query(..., description="Bounding box north"),
    south: float = Query(..., description="Bounding box south"),
    east: float = Query(0.0, description="Bounding box east"),
    west: float = Query(0.0, description="Bounding box west"),
    scale: str = Query("500", description="Earth Engine scale"),
    dataset: str = Query("esa", description="Dataset identifier"),
):
    """Check whether a specific region is already cached server-side."""
    import hashlib
    cache_key = hashlib.md5(
        f"{float(north):.4f}_{float(south):.4f}_{float(east):.4f}_{float(west):.4f}_{dataset}".encode()
    ).hexdigest()

    cached = False
    if CACHE_DIRS and CACHE_DIRS[0].exists():
        cached = (CACHE_DIRS[0] / f"{cache_key}.jbl").exists()

    return JSONResponse(content={
        "cached": cached, "cache_key": cache_key,
        "bbox": {"north": north, "south": south, "east": east, "west": west},
        "dataset": dataset, "scale": scale,
    })
