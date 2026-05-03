"""
routers/cache.py — /api/cache/* endpoints.

Extracted from location_picker.py (backend refactor, step 6).
"""

from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import Any

from app.server.core.cache_inspector import (
    build_tree_node as _build_tree_node,
    read_json_metadata as _read_json_metadata,
    bbox_from_metadata as _bbox_from_metadata,
    match_region_name as _match_region_name,
    infer_region_group as _infer_region_group,
    build_region_tree as _build_region_tree,
    flatten_files as _flatten_files,
)

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

try:
    from app.server.core.cache import CACHE_ROOT as CORE_CACHE_ROOT
except ImportError:
    CORE_CACHE_ROOT = Path(__file__).resolve().parents[3] / "cache"

_last_cache_clear: float = 0.0


def _iter_cache_roots() -> list[Path]:
    """Return unique existing cache directories used by the app."""
    candidates = [CORE_CACHE_ROOT, EE_CACHE_DIR, *CACHE_DIRS]
    dedup: list[Path] = []
    seen: set[str] = set()
    for raw in candidates:
        try:
            p = Path(raw).resolve()
        except Exception:
            continue
        key = str(p).lower()
        if key in seen:
            continue
        seen.add(key)
        if p.exists() and p.is_dir():
            dedup.append(p)
    return dedup


def _load_regions() -> list[dict[str, Any]]:
    try:
        from app.server.core.db import get_db
    except Exception:
        return []

    try:
        with get_db() as conn:
            rows = conn.execute(
                "SELECT name, north, south, east, west FROM regions ORDER BY name"
            ).fetchall()
    except Exception:
        return []

    regions: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["area"] = max(0.0, (item["north"] - item["south"]) * (item["east"] - item["west"]))
        regions.append(item)
    return regions


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
                    pass
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
async def clear_region_cache(request: Request):
    """Clear all namespace caches (DEM, water, satellite, etc.) for a region.

    Expects bbox query params: north, south, east, west.
    """
    from app.server.core.cache import clear_bbox_cache

    params = request.query_params
    try:
        north = float(params["north"])
        south = float(params["south"])
        east = float(params["east"])
        west = float(params["west"])
    except (KeyError, ValueError):
        return JSONResponse(
            content={
                "error": "Missing or invalid bbox parameters (north, south, east, west)"},
            status_code=400)

    results = clear_bbox_cache(north, south, east, west)
    total = sum(results.values())
    return JSONResponse(content={
        "status": "success",
        "files_deleted": total,
        "by_namespace": results,
    })


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


@router.get("/api/cache/inventory")
async def get_cache_inventory():
    """Return full cache file inventory and directory tree for UI visualization."""
    roots = _iter_cache_roots()
    regions = _load_regions()

    root_nodes: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    total_size = 0
    total_files = 0

    for root in roots:
        node = _build_tree_node(root, root)
        if node is None:
            continue
        root_name = root.name
        node["path"] = root_name
        node["root_path"] = str(root)
        root_nodes.append(node)
        _flatten_files(node, root_name, root, regions, files)
        total_size += int(node.get("size_bytes", 0))
        total_files += int(node.get("file_count", 0))

    files.sort(key=lambda f: (str(f.get("region_group", "")), -int(f.get("size_bytes", 0))))
    region_tree = _build_region_tree(files)

    return JSONResponse(content={
        "status": "ok",
        "generated_at": time.time(),
        "total_size_bytes": total_size,
        "total_files": total_files,
        "roots": [
            {
                "name": n.get("name"),
                "path": n.get("root_path", ""),
                "size_bytes": int(n.get("size_bytes", 0)),
                "file_count": int(n.get("file_count", 0)),
            }
            for n in root_nodes
        ],
        "tree": region_tree,
        "physical_tree": {
            "name": "cache",
            "path": "cache",
            "is_dir": True,
            "size_bytes": total_size,
            "file_count": total_files,
            "children": root_nodes,
        },
        "files": files,
    })
