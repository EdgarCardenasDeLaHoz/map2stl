"""
routers/cache.py — /api/cache/* endpoints.

Extracted from location_picker.py (backend refactor, step 6).
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["cache"])

# ---------------------------------------------------------------------------
# Config imports
# ---------------------------------------------------------------------------
try:
    from app.server.config import CACHE_DIRS, CACHE_CLEAR_INTERVAL, CACHE_MAX_FILES, EE_CACHE_DIR
except ImportError:
    _UI_DIR = Path(__file__).parent.parent
    _PROJECT_ROOT = _UI_DIR.parent.parent
    EE_CACHE_DIR = _PROJECT_ROOT / "cache" / "ee"
    CACHE_DIRS = [EE_CACHE_DIR]
    CACHE_CLEAR_INTERVAL = 3600
    CACHE_MAX_FILES = 100

_last_cache_clear: float = 0.0


# ---------------------------------------------------------------------------
# Internal helpers (kept here since they're only used by cache routes)
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
                    pass
            cleared.append({"path": str(cache_dir), "files_deleted": deleted, "total_files": len(cache_files)})
            logger.info(f"Cleared cache: {cache_dir} ({deleted}/{len(cache_files)} files)")
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
            recent = sorted(cache_files, key=lambda f: f.stat().st_mtime, reverse=True)[:5]
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


@router.get("/api/cache/check")
async def check_cache(request: Request):
    """Check whether a specific region is already cached server-side."""
    params = request.query_params
    north = params.get("north")
    south = params.get("south")
    east = params.get("east", "0")
    west = params.get("west", "0")
    scale = params.get("scale", "500")
    dataset = params.get("dataset", "esa")

    if north is None or south is None:
        return JSONResponse(content={"error": "Missing north/south bbox parameters"}, status_code=400)

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
