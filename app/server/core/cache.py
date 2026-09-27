"""
core/cache.py — App-side cache policy for map2stl.

The storage primitives (``CACHE_ROOT``, key helpers, array and OSM cache
read/write) live in ``geo2stl.cache`` so the libraries can use them without
importing the app. This module keeps what only the server needs: pruning by
TTL / file count, the whole-cache clear behind the inspector, and the one-time
migration of legacy plain-JSON OSM files at startup.

``CACHE_ROOT`` is forwarded live from ``geo2stl.cache`` (see ``__getattr__``),
so patching ``geo2stl.cache.CACHE_ROOT`` redirects this module too.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import geo2stl.cache as _gc

# Re-exported for app code; libraries import geo2stl.cache.
from geo2stl.cache import (  # noqa: F401
    NAMESPACE_TTL,
    make_cache_key,
    osm_cache_key,
    read_array_cache,
    read_osm_cache,
    write_array_cache,
    write_osm_cache,
)

logger = logging.getLogger(__name__)

MAX_FILES_PER_NAMESPACE = 200


def __getattr__(name: str):
    if name == "CACHE_ROOT":
        return _gc.CACHE_ROOT
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ---------------------------------------------------------------------------
# Pruning
# ---------------------------------------------------------------------------

def prune_cache(namespace: str, ttl_seconds: int | None = None,
                max_files: int = MAX_FILES_PER_NAMESPACE) -> int:
    """Delete stale or excess entries from *namespace*.

    Returns the number of files deleted.
    """
    d = _gc.CACHE_ROOT / namespace
    if not d.exists():
        return 0

    ttl = ttl_seconds if ttl_seconds is not None else NAMESPACE_TTL.get(
        namespace, 7 * 86400)
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
                logger.debug('Could not delete cache file during prune', exc_info=True)

    if deleted:
        logger.info(f"prune_cache({namespace}): deleted {deleted} files")
    return deleted


def prune_all_caches() -> dict[str, int]:
    """Prune all known namespaces. Returns {namespace: deleted_count}."""
    results: dict[str, int] = {}
    for ns in list(NAMESPACE_TTL.keys()):
        results[ns] = prune_cache(ns)
    return results


def clear_bbox_cache(north: float, south: float,
                     east: float, west: float) -> dict[str, int]:
    """Delete all cached entries across all namespaces.

    Cache keys are MD5 hashes that embed the bbox, so we cannot selectively
    filter without recomputing every possible parameter combination.  Instead,
    delete all files in every namespace directory — this is safe because the
    data will be re-fetched on the next request.

    Returns ``{namespace: deleted_count}``.
    """
    results: dict[str, int] = {}

    for ns in list(NAMESPACE_TTL.keys()):
        d = _gc.CACHE_ROOT / ns
        if not d.exists():
            continue
        deleted = 0
        for f in list(d.iterdir()):
            try:
                f.unlink()
                deleted += 1
            except Exception:
                logger.debug('Could not delete cache file during clear', exc_info=True)
        results[ns] = deleted

    # Also clear the legacy EE cache directory
    ee_dir = _gc.CACHE_ROOT / "ee"
    if ee_dir.exists():
        deleted = 0
        for f in list(ee_dir.iterdir()):
            try:
                f.unlink()
                deleted += 1
            except Exception:
                logger.debug('Could not delete EE cache file during clear', exc_info=True)
        results["ee"] = deleted

    if any(results.values()):
        logger.info(f"clear_bbox_cache: deleted {results}")
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
    new_dir = _gc._osm_dir()
    for old_file in osm_cache_path.glob("*.json"):
        new_file = new_dir / (old_file.stem + ".json.gz")
        if new_file.exists():
            # Already migrated — just remove old file
            try:
                old_file.unlink()
                migrated += 1
            except Exception:
                logger.debug('Could not remove legacy OSM cache file', exc_info=True)
            continue
        try:
            data = json.loads(old_file.read_text())
            write_osm_cache(old_file.stem, data)
            old_file.unlink()
            migrated += 1
        except Exception as e:
            logger.warning(
                f"migrate_osm_plain_json: could not migrate {old_file.name}: {e}")
    if migrated:
        logger.info(
            f"Migrated {migrated} plain-JSON OSM cache files -> .json.gz")
    return migrated
