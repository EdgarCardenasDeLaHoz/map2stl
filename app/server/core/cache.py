"""
core/cache.py — App-side cache policy for map2stl.

The storage primitives (``CACHE_ROOT``, key helpers, array and OSM cache
read/write) live in ``geo2stl.cache`` so the libraries can use them without
importing the app. This module keeps what only the server needs: a fast array
cache writer (zlib level 1 instead of ``np.savez_compressed``'s level 6, plus an
off-event-loop ``write_array_cache_async``), pruning by TTL / entry count, the
whole-cache clear behind the inspector, and the one-time migration of legacy
plain-JSON OSM files at startup.

The fast writer produces an ordinary ``.npz`` (a zip of ``.npy`` members), so
``read_array_cache`` (``np.load``) reads both its files and the level-6 files
the older code wrote; no cache entry is invalidated.

``CACHE_ROOT`` is forwarded live from ``geo2stl.cache`` (see ``__getattr__``),
so patching ``geo2stl.cache.CACHE_ROOT`` redirects this module too.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import zipfile
from pathlib import Path
from typing import Any

import numpy as np

import geo2stl.cache as _gc

# Re-exported for app code; libraries import geo2stl.cache.
from geo2stl.cache import (  # noqa: F401
    NAMESPACE_TTL,
    make_cache_key,
    osm_cache_key,
    read_array_cache,
    read_osm_cache,
    write_osm_cache,
)

logger = logging.getLogger(__name__)

MAX_ENTRIES_PER_NAMESPACE = 200

# zlib level for array cache writes. Level 1 on a 1360x1600 float32 DEM: 0.22 s and
# 4.82 MB, against np.savez_compressed's level 6 at 1.14 s and 4.68 MB. ``None``
# stores the members uncompressed (15 ms write, 14 ms read, but 1.9x the disk, and
# masks that deflate 50x would stay full size).
ARRAY_CACHE_ZLIB_LEVEL: int | None = 1

# A lone .npz / .json half, or a .tmp file, younger than this may be a write in
# progress (the .npz lands before its sidecar), so prune leaves it alone.
ORPHAN_GRACE_SECONDS = 600


def __getattr__(name: str):
    if name == "CACHE_ROOT":
        return _gc.CACHE_ROOT
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ---------------------------------------------------------------------------
# Array cache writes
# ---------------------------------------------------------------------------

def _savez_fast(f, arrays: dict[str, np.ndarray],
                level: int | None = ARRAY_CACHE_ZLIB_LEVEL) -> None:
    """Write *arrays* to file object *f* in ``np.savez`` layout at zlib *level*.

    Same zip-of-``.npy`` members as ``np.savez_compressed``, so ``np.load`` reads
    it unchanged; only the deflate level differs. ``level=None`` stores them.
    """
    compression = zipfile.ZIP_STORED if level is None else zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(f, mode="w", compression=compression,
                         compresslevel=level, allowZip64=True) as zf:
        for name, arr in arrays.items():
            with zf.open(f"{name}.npy", "w", force_zip64=True) as member:
                np.lib.format.write_array(member, np.asanyarray(arr), allow_pickle=False)


def write_array_cache(namespace: str, key: str,
                      arrays: dict[str, np.ndarray],
                      metadata: dict[str, Any] | None = None,
                      keep_dtype: bool = False) -> None:
    """``geo2stl.cache.write_array_cache`` with the fast zlib-level-1 writer.

    Same files and layout (``{key}.npz`` then its ``{key}.json`` sidecar, both
    atomic, sidecar last), same float32 downcast unless ``keep_dtype``. Errors are
    logged and the partial entry removed, never raised.
    """
    d = _gc._array_dir(namespace)
    npz_path = d / f"{key}.npz"
    json_path = d / f"{key}.json"
    try:
        save_dict = {k: (np.asarray(v) if keep_dtype else np.asarray(v).astype(np.float32))
                     for k, v in arrays.items()}
        _gc.atomic_write(npz_path, lambda f: _savez_fast(f, save_dict))
        meta = dict(metadata or {})
        meta["_cached_at"] = time.time()
        _gc.atomic_write(json_path, lambda f: f.write(json.dumps(meta).encode()))
        logger.debug(f"Array cache written: {namespace}/{key} "
                     f"({npz_path.stat().st_size // 1024} KB)")
    except Exception as e:
        logger.warning(f"write_array_cache failed ({namespace}/{key}): {e}")
        for p in (npz_path, json_path):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                logger.debug('Could not delete cache file', exc_info=True)


async def write_array_cache_async(namespace: str, key: str,
                                  arrays: dict[str, np.ndarray],
                                  metadata: dict[str, Any] | None = None,
                                  keep_dtype: bool = False) -> None:
    """Run :func:`write_array_cache` in a worker thread, off the event loop.

    Never raises (the sync writer logs failures). Await it, or wrap it in
    ``asyncio.create_task`` to return first; in that case the caller must not
    mutate *arrays* afterwards, since the thread reads them later.
    """
    await asyncio.to_thread(write_array_cache, namespace, key, arrays,
                            metadata, keep_dtype)


# ---------------------------------------------------------------------------
# Pruning
# ---------------------------------------------------------------------------

def _entry_stem(name: str) -> str:
    """Cache key of a file: everything before the first dot.

    ``k.npz`` + ``k.json`` (array entry), ``k.json.gz`` (+ ``k.params.json`` in
    osm), ``k.tif`` (opentopo) and ``k.npz.<pid>.<tid>.tmp`` all map to ``k``.
    """
    return name.split(".", 1)[0]


def _is_orphan(stem: str, names: set[str]) -> bool:
    """True if the files of one entry are an incomplete half.

    An ``.npz`` without its ``.json`` sidecar (``read_array_cache`` treats it as
    stale forever), or only sidecars (``.json`` / ``.params.json``) with no payload.
    """
    if f"{stem}.npz" in names and f"{stem}.json" not in names:
        return True
    sidecars = {f"{stem}.json", f"{stem}.params.json"}
    return names <= sidecars


def prune_cache(namespace: str, ttl_seconds: int | None = None,
                max_entries: int = MAX_ENTRIES_PER_NAMESPACE,
                max_bytes: int | None = None) -> int:
    """Delete stale, orphaned or excess entries from ``CACHE_ROOT/namespace``.

    Files are grouped into entries by key (:func:`_entry_stem`), and an entry's
    files are always deleted together. In order:

    1. ``.tmp`` files and orphan halves (:func:`_is_orphan`) older than
       ``ORPHAN_GRACE_SECONDS``;
    2. entries whose newest file is older than the TTL (*ttl_seconds*, else
       ``NAMESPACE_TTL``, else 7 days);
    3. oldest entries until at most *max_entries* remain and, when *max_bytes*
       is given, their total size is at most *max_bytes*.

    Only plain files directly inside the namespace directory are touched.
    Returns the number of files deleted.
    """
    d = _gc.CACHE_ROOT / namespace
    if not d.is_dir():
        return 0

    ttl = ttl_seconds if ttl_seconds is not None else NAMESPACE_TTL.get(
        namespace, 7 * 86400)
    now = time.time()
    deleted = 0

    def _unlink(paths) -> None:
        nonlocal deleted
        for p in paths:
            try:
                p.unlink()
                deleted += 1
            except FileNotFoundError:
                pass
            except OSError:
                logger.debug('Could not delete cache file during prune', exc_info=True)

    # Group files by key, recording (mtime, size) once per file.
    groups: dict[str, list[tuple[Path, float, int]]] = {}
    for f in d.iterdir():
        try:
            if not f.is_file():
                continue
            st = f.stat()
        except OSError:
            continue
        if f.name.endswith(".tmp"):
            if now - st.st_mtime > ORPHAN_GRACE_SECONDS:
                _unlink([f])
            continue
        groups.setdefault(_entry_stem(f.name), []).append((f, st.st_mtime, st.st_size))

    surviving: list[tuple[float, int, list[Path]]] = []
    for stem, items in groups.items():
        paths = [p for p, _, _ in items]
        newest = max(m for _, m, _ in items)
        age = now - newest
        if age > ttl or (age > ORPHAN_GRACE_SECONDS
                         and _is_orphan(stem, {p.name for p in paths})):
            _unlink(paths)
        else:
            surviving.append((newest, sum(s for _, _, s in items), paths))

    # Over a cap: evict whole entries, oldest first.
    surviving.sort(key=lambda e: e[0])
    total = sum(e[1] for e in surviving)
    n = len(surviving)
    for _, size, paths in surviving:
        if n <= max_entries and (max_bytes is None or total <= max_bytes):
            break
        _unlink(paths)
        n -= 1
        total -= size

    if deleted:
        logger.info(f"prune_cache({namespace}): deleted {deleted} files")
    return deleted


def prune_all_caches() -> dict[str, int]:
    """Prune all known namespaces. Returns {namespace: deleted_count}.

    Only the namespaces in ``NAMESPACE_TTL``: ``esa_lc``, ``ee``, ``ndsm``,
    ``dem_handles`` and others have no TTL and are never pruned (giving every
    namespace a TTL and a byte ceiling is plan F-DATADIR's job).
    """
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

