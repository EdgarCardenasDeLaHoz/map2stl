"""
core/cache.py — Disk cache helpers for strm2stl.

Two storage formats:
  • Array cache  (.npz + .json)  — for DEM / water-mask / satellite arrays
  • OSM cache    (.json.gz)      — for compressed GeoJSON blobs

Cache key scheme
----------------
  make_cache_key(namespace, bbox, extra_params) → 32-char hex string
  MD5(namespace + ":" + "N{n:.4f}_S{s:.4f}_E{e:.4f}_W{w:.4f}" + ":" + sorted_json(extra_params))

Directory layout (under project_root/cache/)
--------------------------------------------
  cache/
  ├── dem/       {key}.npz  +  {key}.json
  ├── water/     {key}.npz  +  {key}.json
  ├── satellite/ {key}.npz  +  {key}.json
  └── osm/       {key}.json.gz
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Project-level cache root (sibling of strm2stl/)
_PROJECT_ROOT = Path(__file__).parent.parent.parent
CACHE_ROOT = _PROJECT_ROOT / "cache"

# Per-namespace TTLs in seconds
NAMESPACE_TTL = {
    "dem":       30 * 86400,   # 30 days
    "water":     14 * 86400,   # 14 days
    "satellite": 14 * 86400,   # 14 days
    "osm":        7 * 86400,   #  7 days
    "composite": 30 * 86400,   # 30 days (city rasters tied to OSM data)
}
MAX_FILES_PER_NAMESPACE = 200


# ---------------------------------------------------------------------------
# Cache key generation
# ---------------------------------------------------------------------------

def make_cache_key(namespace: str, north: float, south: float,
                   east: float, west: float, extra: dict | None = None) -> str:
    """Return a 32-char MD5 hex string for the given inputs."""
    bbox_str = f"N{north:.4f}_S{south:.4f}_E{east:.4f}_W{west:.4f}"
    extra_str = json.dumps(extra or {}, sort_keys=True, separators=(',', ':'))
    raw = f"{namespace}:{bbox_str}:{extra_str}"
    return hashlib.md5(raw.encode()).hexdigest()


def osm_cache_key(north: float, south: float, east: float, west: float,
                  tol: float = 0.5, min_area: float = 5.0) -> str:
    """Return the MD5 key used by the OSM cache for a given bbox + simplification params.

    Matches the key written by routers/cities.py so other routers can read OSM
    data without re-fetching it.
    """
    return hashlib.md5(
        f"{north:.4f}_{south:.4f}_{east:.4f}_{west:.4f}_t{tol}_a{min_area}".encode()
    ).hexdigest()


# ---------------------------------------------------------------------------
# Array cache (.npz + .json sidecar)
# ---------------------------------------------------------------------------

def _array_dir(namespace: str) -> Path:
    d = CACHE_ROOT / namespace
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_array_cache(namespace: str, key: str,
                      arrays: dict[str, np.ndarray],
                      metadata: dict[str, Any] | None = None) -> None:
    """Save ``arrays`` as float32 .npz and ``metadata`` as .json sidecar."""
    d = _array_dir(namespace)
    npz_path = d / f"{key}.npz"
    json_path = d / f"{key}.json"
    try:
        # Downcast to float32 to keep files small
        save_dict = {k: v.astype(np.float32) for k, v in arrays.items()}
        np.savez_compressed(str(npz_path), **save_dict)
        meta = dict(metadata or {})
        meta["_cached_at"] = time.time()
        json_path.write_text(json.dumps(meta))
        logger.debug(f"Array cache written: {namespace}/{key} "
                     f"({npz_path.stat().st_size // 1024} KB)")
    except Exception as e:
        logger.warning(f"write_array_cache failed ({namespace}/{key}): {e}")
        # Clean up partial writes
        for p in (npz_path, json_path):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass


def read_array_cache(namespace: str, key: str) -> tuple[dict[str, np.ndarray], dict] | None:
    """Return (arrays_dict, metadata) or None if not cached / stale."""
    d = _array_dir(namespace)
    npz_path = d / f"{key}.npz"
    json_path = d / f"{key}.json"
    if not npz_path.exists():
        return None
    try:
        meta: dict = json.loads(json_path.read_text()) if json_path.exists() else {}
        # TTL check
        cached_at = meta.get("_cached_at", 0)
        ttl = NAMESPACE_TTL.get(namespace, 7 * 86400)
        if time.time() - cached_at > ttl:
            logger.debug(f"Array cache stale: {namespace}/{key}")
            return None
        loaded = np.load(str(npz_path))
        arrays = {k: loaded[k] for k in loaded.files}
        logger.debug(f"Array cache hit: {namespace}/{key}")
        return arrays, meta
    except Exception as e:
        logger.warning(f"read_array_cache failed ({namespace}/{key}): {e}")
        return None


# ---------------------------------------------------------------------------
# OSM / GeoJSON cache (.json.gz)
# ---------------------------------------------------------------------------

def _osm_dir() -> Path:
    d = CACHE_ROOT / "osm"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_osm_cache(key: str, data: dict) -> None:
    """Save GeoJSON dict as gzip-compressed JSON."""
    path = _osm_dir() / f"{key}.json.gz"
    try:
        compressed = gzip.compress(json.dumps(data).encode("utf-8"), compresslevel=6)
        path.write_bytes(compressed)
        logger.debug(f"OSM cache written: {key} ({len(compressed) // 1024} KB gz)")
    except Exception as e:
        logger.warning(f"write_osm_cache failed ({key}): {e}")
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass


def read_osm_cache(key: str) -> dict | None:
    """Return parsed GeoJSON dict or None if not cached / stale."""
    path = _osm_dir() / f"{key}.json.gz"
    if not path.exists():
        return None
    try:
        ttl = NAMESPACE_TTL.get("osm", 7 * 86400)
        age = time.time() - path.stat().st_mtime
        if age > ttl:
            logger.debug(f"OSM cache stale: {key}")
            return None
        data = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
        logger.debug(f"OSM cache hit: {key}")
        return data
    except Exception as e:
        logger.warning(f"read_osm_cache failed ({key}): {e}")
        return None


# ---------------------------------------------------------------------------
# Pruning
# ---------------------------------------------------------------------------

def prune_cache(namespace: str, ttl_seconds: int | None = None,
                max_files: int = MAX_FILES_PER_NAMESPACE) -> int:
    """Delete stale or excess entries from *namespace*.

    Returns the number of files deleted.
    """
    d = CACHE_ROOT / namespace
    if not d.exists():
        return 0

    ttl = ttl_seconds if ttl_seconds is not None else NAMESPACE_TTL.get(namespace, 7 * 86400)
    now = time.time()
    deleted = 0

    # Collect all logical entries (de-duplicate .npz / .json sidecar pairs)
    files = sorted(d.iterdir(), key=lambda f: f.stat().st_mtime)

    # Delete files older than TTL first
    surviving: list[Path] = []
    for f in files:
        try:
            if now - f.stat().st_mtime > ttl:
                f.unlink()
                deleted += 1
            else:
                surviving.append(f)
        except Exception:
            surviving.append(f)

    # If still over max_files, remove oldest
    if len(surviving) > max_files:
        for f in surviving[:len(surviving) - max_files]:
            try:
                f.unlink()
                deleted += 1
            except Exception:
                pass

    if deleted:
        logger.info(f"prune_cache({namespace}): deleted {deleted} files")
    return deleted


def prune_all_caches() -> dict[str, int]:
    """Prune all known namespaces. Returns {namespace: deleted_count}."""
    results: dict[str, int] = {}
    for ns in list(NAMESPACE_TTL.keys()):
        results[ns] = prune_cache(ns)
    return results


# ---------------------------------------------------------------------------
# OSM legacy migration
# ---------------------------------------------------------------------------

def migrate_osm_plain_json(osm_cache_path: Path) -> int:
    """One-time migration: compress any plain .json files in the old OSM cache dir.

    Reads each *.json file, writes it as *.json.gz into the new cache location,
    then deletes the old file.  Returns the number of files migrated.
    """
    if not osm_cache_path.exists():
        return 0
    migrated = 0
    new_dir = _osm_dir()
    for old_file in osm_cache_path.glob("*.json"):
        new_file = new_dir / (old_file.stem + ".json.gz")
        if new_file.exists():
            # Already migrated — just remove old file
            try:
                old_file.unlink()
                migrated += 1
            except Exception:
                pass
            continue
        try:
            data = json.loads(old_file.read_text())
            write_osm_cache(old_file.stem, data)
            old_file.unlink()
            migrated += 1
        except Exception as e:
            logger.warning(f"migrate_osm_plain_json: could not migrate {old_file.name}: {e}")
    if migrated:
        logger.info(f"Migrated {migrated} plain-JSON OSM cache files → .json.gz")
    return migrated
