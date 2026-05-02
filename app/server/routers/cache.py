"""
routers/cache.py — /api/cache/* endpoints.

Extracted from location_picker.py (backend refactor, step 6).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

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


def _build_tree_node(path: Path, root: Path) -> dict[str, Any] | None:
    """Build a recursive folder/file tree node with size totals."""
    try:
        rel = path.relative_to(root)
        rel_str = "." if str(rel) == "." else str(rel).replace("\\", "/")
    except Exception:
        rel_str = path.name

    if path.is_file():
        try:
            size = int(path.stat().st_size)
            mtime = float(path.stat().st_mtime)
        except Exception:
            return None
        return {
            "name": path.name,
            "path": rel_str,
            "is_dir": False,
            "size_bytes": size,
            "file_count": 1,
            "mtime": mtime,
            "children": [],
        }

    children: list[dict[str, Any]] = []
    size_sum = 0
    file_count = 0
    try:
        entries = sorted(path.iterdir(), key=lambda p: p.name.lower())
    except Exception:
        entries = []

    for entry in entries:
        node = _build_tree_node(entry, root)
        if node is None:
            continue
        children.append(node)
        size_sum += int(node["size_bytes"])
        file_count += int(node["file_count"])

    children.sort(key=lambda n: int(n.get("size_bytes", 0)), reverse=True)
    return {
        "name": path.name,
        "path": rel_str,
        "is_dir": True,
        "size_bytes": size_sum,
        "file_count": file_count,
        "children": children,
    }


_HYDRORIVERS_REGION_NAMES = {
    "af": "HydroRIVERS Africa",
    "ar": "HydroRIVERS Arctic",
    "as": "HydroRIVERS Asia",
    "au": "HydroRIVERS Australia",
    "eu": "HydroRIVERS Europe",
    "na": "HydroRIVERS North America",
    "sa": "HydroRIVERS South America",
    "si": "HydroRIVERS Siberia",
}


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


def _read_json_metadata(path: Path) -> dict[str, Any] | None:
    try:
        if not path.exists() or path.stat().st_size > 256 * 1024:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _bbox_from_metadata(meta: dict[str, Any] | None) -> tuple[float, float, float, float] | None:
    if not meta:
        return None

    bbox = meta.get("bbox")
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        west, south, east, north = bbox
        try:
            return float(west), float(south), float(east), float(north)
        except Exception:
            return None

    try:
        return (
            float(meta["west"]),
            float(meta["south"]),
            float(meta["east"]),
            float(meta["north"]),
        )
    except Exception:
        return None


def _match_region_name(bbox: tuple[float, float, float, float] | None,
                       regions: list[dict[str, Any]]) -> str | None:
    if bbox is None:
        return None

    west, south, east, north = bbox
    containing: list[dict[str, Any]] = []
    intersecting: list[tuple[float, dict[str, Any]]] = []

    for region in regions:
        if west >= region["west"] and east <= region["east"] and south >= region["south"] and north <= region["north"]:
            containing.append(region)
            continue

        overlap_west = max(west, region["west"])
        overlap_south = max(south, region["south"])
        overlap_east = min(east, region["east"])
        overlap_north = min(north, region["north"])
        if overlap_west < overlap_east and overlap_south < overlap_north:
            overlap_area = (overlap_east - overlap_west) * (overlap_north - overlap_south)
            intersecting.append((overlap_area, region))

    if containing:
        containing.sort(key=lambda region: (region.get("area", 0.0), region["name"]))
        return str(containing[0]["name"])
    if intersecting:
        intersecting.sort(key=lambda item: (-item[0], item[1]["name"]))
        return str(intersecting[0][1]["name"])
    return None


def _infer_region_group(abs_path: Path, relative_path: str, namespace: str,
                        regions: list[dict[str, Any]]) -> str:
    parts = [part for part in relative_path.replace("\\", "/").split("/") if part]
    if namespace == "hydrorivers":
        for part in parts:
            if part.startswith("HydroRIVERS_v10_"):
                code = part.split("_")[2][:2].lower()
                return _HYDRORIVERS_REGION_NAMES.get(code, "HydroRIVERS Shared")
        return "HydroRIVERS Shared"

    meta: dict[str, Any] | None = None
    if abs_path.suffix.lower() == ".json":
        meta = _read_json_metadata(abs_path)
    elif abs_path.suffix.lower() == ".npz":
        meta = _read_json_metadata(abs_path.with_suffix(".json"))

    region_name = _match_region_name(_bbox_from_metadata(meta), regions)
    if region_name:
        return region_name

    if namespace in {"dem", "water", "esa_lc", "composite", "osm", "ghsl", "ndsm", "shadow_height", "wsf3d"}:
        return "Shared / Unmatched"
    if namespace.startswith("height_tiles"):
        return "Training / ML"
    return "Shared / Global"


def _build_region_tree(files: list[dict[str, Any]]) -> dict[str, Any]:
    root = {
        "name": "cache",
        "path": "cache",
        "is_dir": True,
        "size_bytes": sum(int(file.get("size_bytes", 0)) for file in files),
        "file_count": len(files),
        "children": [],
    }

    region_groups: dict[str, list[dict[str, Any]]] = {}
    for file in files:
        region_groups.setdefault(str(file.get("region_group") or "Shared / Global"), []).append(file)

    for region_name in sorted(region_groups.keys(), key=lambda value: (value.startswith("Shared"), value.lower())):
        region_files = region_groups[region_name]
        region_node = {
            "name": region_name,
            "path": region_name,
            "is_dir": True,
            "size_bytes": sum(int(file.get("size_bytes", 0)) for file in region_files),
            "file_count": len(region_files),
            "children": [],
        }

        namespace_groups: dict[str, list[dict[str, Any]]] = {}
        for file in region_files:
            namespace_groups.setdefault(str(file.get("namespace") or file.get("root") or "cache"), []).append(file)

        for namespace in sorted(namespace_groups.keys(), key=str.lower):
            namespace_files = namespace_groups[namespace]
            namespace_node = {
                "name": namespace,
                "path": f"{region_name}/{namespace}",
                "is_dir": True,
                "size_bytes": sum(int(file.get("size_bytes", 0)) for file in namespace_files),
                "file_count": len(namespace_files),
                "children": [],
            }
            for file in sorted(namespace_files, key=lambda item: int(item.get("size_bytes", 0)), reverse=True):
                namespace_node["children"].append({
                    "name": str(file.get("name") or ""),
                    "path": str(file.get("relative_path") or file.get("name") or ""),
                    "is_dir": False,
                    "size_bytes": int(file.get("size_bytes", 0)),
                    "file_count": 1,
                    "mtime": float(file.get("mtime", 0.0)),
                    "children": [],
                })
            region_node["children"].append(namespace_node)

        root["children"].append(region_node)

    return root


def _flatten_files(node: dict[str, Any], root_name: str, root_path: Path,
                   regions: list[dict[str, Any]], out: list[dict[str, Any]]) -> None:
    """Flatten tree leaves into file records for table display."""
    if not node.get("is_dir"):
        rel = str(node.get("path", ""))
        namespace = rel.replace("\\", "/").split("/", 1)[0] if rel else root_name
        abs_path = root_path / rel if rel and rel != "." else root_path
        out.append({
            "root": root_name,
            "namespace": namespace,
            "region_group": _infer_region_group(abs_path, rel, namespace, regions),
            "name": node.get("name", ""),
            "relative_path": rel,
            "size_bytes": int(node.get("size_bytes", 0)),
            "mtime": float(node.get("mtime", 0.0)),
        })
        return
    for child in node.get("children", []):
        _flatten_files(child, root_name, root_path, regions, out)


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
