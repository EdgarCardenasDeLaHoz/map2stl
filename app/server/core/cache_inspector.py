"""
core/cache_inspector.py — Filesystem tree building and cache metadata helpers.

Extracted from routers/cache.py so these pure filesystem/inspection utilities
can be tested and reused without importing FastAPI router machinery.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Filesystem tree helpers
# ---------------------------------------------------------------------------

def build_tree_node(path: Path, root: Path) -> dict[str, Any] | None:
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
        node = build_tree_node(entry, root)
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


# ---------------------------------------------------------------------------
# Metadata / bbox helpers
# ---------------------------------------------------------------------------

def read_json_metadata(path: Path) -> dict[str, Any] | None:
    try:
        if not path.exists() or path.stat().st_size > 256 * 1024:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def bbox_from_metadata(meta: dict[str, Any] | None) -> tuple[float, float, float, float] | None:
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


# ---------------------------------------------------------------------------
# Region matching / grouping
# ---------------------------------------------------------------------------

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


def match_region_name(
    bbox: tuple[float, float, float, float] | None,
    regions: list[dict[str, Any]],
) -> str | None:
    if bbox is None:
        return None

    west, south, east, north = bbox
    containing: list[dict[str, Any]] = []
    intersecting: list[tuple[float, dict[str, Any]]] = []

    for region in regions:
        if (west >= region["west"] and east <= region["east"]
                and south >= region["south"] and north <= region["north"]):
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
        containing.sort(key=lambda r: (r.get("area", 0.0), r["name"]))
        return str(containing[0]["name"])
    if intersecting:
        intersecting.sort(key=lambda item: (-item[0], item[1]["name"]))
        return str(intersecting[0][1]["name"])
    return None


def infer_region_group(
    abs_path: Path,
    relative_path: str,
    namespace: str,
    regions: list[dict[str, Any]],
) -> str:
    parts = [p for p in relative_path.replace("\\", "/").split("/") if p]
    if namespace == "hydrorivers":
        for part in parts:
            if part.startswith("HydroRIVERS_v10_"):
                code = part.split("_")[2][:2].lower()
                return _HYDRORIVERS_REGION_NAMES.get(code, "HydroRIVERS Shared")
        return "HydroRIVERS Shared"

    meta: dict[str, Any] | None = None
    if abs_path.suffix.lower() == ".json":
        meta = read_json_metadata(abs_path)
    elif abs_path.suffix.lower() == ".npz":
        meta = read_json_metadata(abs_path.with_suffix(".json"))

    region_name = match_region_name(bbox_from_metadata(meta), regions)
    if region_name:
        return region_name

    if namespace in {"dem", "water", "esa_lc", "composite", "osm", "ghsl", "ndsm",
                     "shadow_height", "wsf3d"}:
        return "Shared / Unmatched"
    if namespace.startswith("height_tiles"):
        return "Training / ML"
    return "Shared / Global"


# ---------------------------------------------------------------------------
# Display tree builders
# ---------------------------------------------------------------------------

def build_region_tree(files: list[dict[str, Any]]) -> dict[str, Any]:
    root: dict[str, Any] = {
        "name": "cache",
        "path": "cache",
        "is_dir": True,
        "size_bytes": sum(int(f.get("size_bytes", 0)) for f in files),
        "file_count": len(files),
        "children": [],
    }

    region_groups: dict[str, list[dict[str, Any]]] = {}
    for f in files:
        region_groups.setdefault(str(f.get("region_group") or "Shared / Global"), []).append(f)

    for region_name in sorted(region_groups, key=lambda v: (v.startswith("Shared"), v.lower())):
        region_files = region_groups[region_name]
        region_node: dict[str, Any] = {
            "name": region_name,
            "path": region_name,
            "is_dir": True,
            "size_bytes": sum(int(f.get("size_bytes", 0)) for f in region_files),
            "file_count": len(region_files),
            "children": [],
        }

        namespace_groups: dict[str, list[dict[str, Any]]] = {}
        for f in region_files:
            namespace_groups.setdefault(
                str(f.get("namespace") or f.get("root") or "cache"), []
            ).append(f)

        for namespace in sorted(namespace_groups, key=str.lower):
            namespace_files = namespace_groups[namespace]
            namespace_node: dict[str, Any] = {
                "name": namespace,
                "path": f"{region_name}/{namespace}",
                "is_dir": True,
                "size_bytes": sum(int(f.get("size_bytes", 0)) for f in namespace_files),
                "file_count": len(namespace_files),
                "children": [],
            }
            for f in sorted(namespace_files, key=lambda item: int(item.get("size_bytes", 0)), reverse=True):
                namespace_node["children"].append({
                    "name": str(f.get("name") or ""),
                    "path": str(f.get("relative_path") or f.get("name") or ""),
                    "is_dir": False,
                    "size_bytes": int(f.get("size_bytes", 0)),
                    "file_count": 1,
                    "mtime": float(f.get("mtime", 0.0)),
                    "children": [],
                })
            region_node["children"].append(namespace_node)

        root["children"].append(region_node)

    return root


def flatten_files(
    node: dict[str, Any],
    root_name: str,
    root_path: Path,
    regions: list[dict[str, Any]],
    out: list[dict[str, Any]],
) -> None:
    """Flatten tree leaves into file records for table display."""
    if not node.get("is_dir"):
        rel = str(node.get("path", ""))
        namespace = rel.replace("\\", "/").split("/", 1)[0] if rel else root_name
        abs_path = root_path / rel if rel and rel != "." else root_path
        out.append({
            "root": root_name,
            "namespace": namespace,
            "region_group": infer_region_group(abs_path, rel, namespace, regions),
            "name": node.get("name", ""),
            "relative_path": rel,
            "size_bytes": int(node.get("size_bytes", 0)),
            "mtime": float(node.get("mtime", 0.0)),
        })
        return
    for child in node.get("children", []):
        flatten_files(child, root_name, root_path, regions, out)
