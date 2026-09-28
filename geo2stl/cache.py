"""
geo2stl/cache.py — Disk cache primitives shared by the libraries and the app.

Two storage formats:
  • Array cache  (.npz + .json)  — for DEM / water-mask / satellite / height arrays
  • OSM cache    (.json.gz)      — for compressed GeoJSON blobs

Cache key scheme
----------------
  make_cache_key(namespace, bbox, extra_params) → 32-char hex string
  MD5(namespace + ":" + "N{n:.4f}_S{s:.4f}_E{e:.4f}_W{w:.4f}" + ":" + sorted_json(extra_params))

Directory layout (under map2stl/cache/, or $MAP2STL_CACHE)
------------------------------------------------------------
  cache/
  ├── dem/        {key}.npz  +  {key}.json
  ├── water/      {key}.npz  +  {key}.json
  ├── satellite/  {key}.npz  +  {key}.json
  ├── osm/        {key}.json.gz  (+ {key}.params.json: its bbox, tol, min_area)
  ├── geocode/    {key}.json.gz   (JSON cache: Nominatim search results)
  ├── landmarks/  {key}.json.gz   (JSON cache: notable OSM features near a bbox)
  └── opentopo/   {key}.tif  (raw GeoTIFFs from OpenTopography API)

Every function reads the module-global ``CACHE_ROOT`` at call time, so tests
redirect the whole cache by patching ``geo2stl.cache.CACHE_ROOT``. Pruning,
the cache inspector and the startup migration are app concerns and live in
``app/server/core/cache.py``.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# map2stl/ root  (geo2stl/cache.py → geo2stl → map2stl)
_MAP2STL_DIR = Path(__file__).resolve().parents[1]

# Cache location. Defaults to map2stl/cache, but can be redirected OUTSIDE the
# project tree via MAP2STL_CACHE — recommended so the (large, regeneratable)
# cache isn't synced by OneDrive. e.g. setx MAP2STL_CACHE "%LOCALAPPDATA%\map2stl\cache"
_CACHE_ENV = os.environ.get("MAP2STL_CACHE") or os.environ.get("STRM2STL_CACHE")
CACHE_ROOT = Path(_CACHE_ENV).expanduser() if _CACHE_ENV else _MAP2STL_DIR / "cache"

# Per-namespace TTLs in seconds
NAMESPACE_TTL = {
    "dem":        30 * 86400,   # 30 days
    "water":      14 * 86400,   # 14 days
    "satellite":  14 * 86400,   # 14 days
    "osm":         7 * 86400,  # 7 days
    "composite":  30 * 86400,   # 30 days (city rasters tied to OSM data)
    "opentopo":   90 * 86400,   # 90 days (raw GeoTIFFs rarely change)
    "hydrology":  30 * 86400,   # 30 days (river network rarely changes)
    "trails":      7 * 86400,   # 7 days (OSM-derived; tracks OSM's own TTL)
    "trails_osm":  7 * 86400,   # 7 days (trail FeatureCollections, geo2stl.trails)
    "geocode":    30 * 86400,   # 30 days (Nominatim search results)
    "landmarks":   7 * 86400,   # 7 days (OSM notable features near a bbox edge)
    # city2stl.model_cache: derived city-model geometry (keyed by content digests)
    "city_polygons": 30 * 86400,  # layer polygons after print-scale simplification
    "city_solids":   30 * 86400,  # finished per-layer union solids
    "city_terrain":  30 * 86400,  # adaptive terrain TIN solids
    "city_models":   30 * 86400,  # finished models (merged + parts) for unchanged inputs
}


def _is_stale(cached_at: float, namespace: str) -> bool:
    """Return True if a cache entry's timestamp exceeds its namespace TTL."""
    ttl = NAMESPACE_TTL.get(namespace, 7 * 86400)
    return time.time() - cached_at > ttl


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

    Matches the key written by the app's city router, so any caller can read
    OSM data without re-fetching it.
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


# Windows attributes OneDrive sets on a cloud-only ("Files On-Demand") placeholder:
# RECALL_ON_DATA_ACCESS | RECALL_ON_OPEN | OFFLINE.
_PLACEHOLDER_ATTRS = 0x400000 | 0x40000 | 0x1000


def _is_cloud_placeholder(path: Path) -> bool:
    """True if *path* is a dehydrated OneDrive placeholder.

    Opening one needs the OneDrive client to download it first; from a process it
    will not hydrate for (the MSIX-sandboxed desktop app, or with OneDrive not
    running) ``open()`` fails with ``[Errno 22] Invalid argument``.
    """
    try:
        return bool(getattr(path.stat(), "st_file_attributes", 0) & _PLACEHOLDER_ATTRS)
    except OSError:
        return False


def _atomic_write(path: Path, write) -> None:
    """Call ``write(fileobj)`` on a temp file beside *path*, then rename it over *path*.

    A reader never sees a half-written entry: two requests for the same bbox (or
    tile) can fetch concurrently, and a crash mid-write used to leave a truncated
    .npz that failed every later read.
    """
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with open(tmp, "wb") as f:
            write(f)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def write_array_cache(namespace: str, key: str,
                      arrays: dict[str, np.ndarray],
                      metadata: dict[str, Any] | None = None,
                      keep_dtype: bool = False) -> None:
    """Save ``arrays`` as float32 .npz and ``metadata`` as .json sidecar.

    ``keep_dtype`` stores the arrays as they are (meshes: float64 vertices that
    must stay bit-exact, integer faces). Both files are written atomically; the
    sidecar goes last, so its presence means the .npz beside it is complete.
    """
    d = _array_dir(namespace)
    npz_path = d / f"{key}.npz"
    json_path = d / f"{key}.json"
    try:
        # Downcast to float32 to keep files small
        save_dict = {k: (np.asarray(v) if keep_dtype else np.asarray(v).astype(np.float32))
                     for k, v in arrays.items()}
        _atomic_write(npz_path, lambda f: np.savez_compressed(f, **save_dict))
        meta = dict(metadata or {})
        meta["_cached_at"] = time.time()
        _atomic_write(json_path, lambda f: f.write(json.dumps(meta).encode()))
        logger.debug(f"Array cache written: {namespace}/{key} "
                     f"({npz_path.stat().st_size // 1024} KB)")
    except Exception as e:
        logger.warning(f"write_array_cache failed ({namespace}/{key}): {e}")
        # Clean up partial writes
        for p in (npz_path, json_path):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                logger.debug('Could not delete cache file', exc_info=True)


def read_array_cache(namespace: str, key: str) -> tuple[dict[str, np.ndarray], dict] | None:
    """Return (arrays_dict, metadata) or None if not cached / stale."""
    d = _array_dir(namespace)
    npz_path = d / f"{key}.npz"
    json_path = d / f"{key}.json"
    if not npz_path.exists():
        return None
    if _is_cloud_placeholder(npz_path) or _is_cloud_placeholder(json_path):
        # OneDrive dehydrated the entry and this process cannot recall it. Drop it
        # so the caller's re-fetch writes a local copy instead of hitting this again.
        logger.info(f"Array cache entry is a cloud-only OneDrive placeholder, "
                    f"re-fetching: {namespace}/{key}")
        for p in (npz_path, json_path):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                logger.debug('Could not delete placeholder cache file', exc_info=True)
        return None
    try:
        meta: dict = json.loads(json_path.read_text()
                                ) if json_path.exists() else {}
        # TTL check
        if _is_stale(meta.get("_cached_at", 0), namespace):
            logger.debug(f"Array cache stale: {namespace}/{key}")
            return None
        with np.load(str(npz_path)) as loaded:
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


def write_osm_cache(key: str, data: dict, params: dict | None = None) -> None:
    """Save GeoJSON dict as gzip-compressed JSON.

    ``params`` (``north/south/east/west/tol/min_area``, as passed to
    :func:`osm_cache_key`) is written beside the entry as ``{key}.params.json``:
    the key is a hash, so without it a reader looking for *another* entry for the
    same (or an enclosing) bbox would have to decompress every payload
    (:func:`list_osm_cache_params`).
    """
    path = _osm_dir() / f"{key}.json.gz"
    try:
        compressed = gzip.compress(json.dumps(
            data).encode("utf-8"), compresslevel=6)
        _atomic_write(path, lambda f: f.write(compressed))
        logger.debug(
            f"OSM cache written: {key} ({len(compressed) // 1024} KB gz)")
        if params is not None:
            write_osm_cache_params(key, params)
    except Exception as e:
        logger.warning(f"write_osm_cache failed ({key}): {e}")
        try:
            path.unlink(missing_ok=True)
        except Exception:
            logger.debug('Could not delete stale OSM cache file', exc_info=True)


def write_osm_cache_params(key: str, params: dict) -> None:
    """Record which bbox and simplification an OSM cache entry holds (sidecar)."""
    blob = json.dumps({k: v for k, v in params.items() if k != "key"}).encode()
    try:
        _atomic_write(_osm_dir() / f"{key}.params.json", lambda f: f.write(blob))
    except OSError as e:
        logger.warning(f"write_osm_cache_params failed ({key}): {e}")


def list_osm_cache_params() -> list[dict]:
    """``[{key, north, south, east, west, tol, min_area, ...}]`` of every OSM cache
    entry that has a params sidecar and is present and within the TTL."""
    out = []
    d = _osm_dir()
    for p in d.glob("*.params.json"):
        key = p.name[: -len(".params.json")]
        gz = d / f"{key}.json.gz"
        try:
            if not gz.exists() or _is_stale(gz.stat().st_mtime, "osm"):
                continue
            meta = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(meta, dict):
            out.append({**meta, "key": key})
    return out


def read_osm_cache(key: str, allow_stale: bool = False) -> dict | None:
    """Return parsed GeoJSON dict or None if not cached / stale.

    ``allow_stale`` ignores the TTL. Intended for callers that have already
    tried and failed to fetch fresh data: Overpass goes down often enough that
    refusing a two-month-old building footprint — for buildings that have not
    moved — turns a mirror outage into a failed run.
    """
    path = _osm_dir() / f"{key}.json.gz"
    if not path.exists():
        return None
    try:
        if _is_stale(path.stat().st_mtime, "osm") and not allow_stale:
            logger.debug(f"OSM cache stale: {key}")
            return None
        data = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
        logger.debug(f"OSM cache hit: {key}")
        return data
    except Exception as e:
        logger.warning(f"read_osm_cache failed ({key}): {e}")
        return None


# ---------------------------------------------------------------------------
# Small JSON cache (.json.gz) in any namespace
# ---------------------------------------------------------------------------

def json_cache_key(namespace: str, *parts: Any) -> str:
    """MD5 key for a JSON cache entry built from *parts* (query text, rounded bbox...)."""
    raw = namespace + ":" + json.dumps(parts, sort_keys=True, separators=(",", ":"))
    return hashlib.md5(raw.encode()).hexdigest()


def write_json_cache(namespace: str, key: str, data: Any) -> None:
    """Save *data* as gzip-compressed JSON under ``CACHE_ROOT/namespace``."""
    path = _array_dir(namespace) / f"{key}.json.gz"
    try:
        blob = gzip.compress(json.dumps(data).encode("utf-8"), compresslevel=6)
        _atomic_write(path, lambda f: f.write(blob))
    except Exception as e:
        logger.warning(f"write_json_cache failed ({namespace}/{key}): {e}")


def read_json_cache(namespace: str, key: str) -> Any | None:
    """Return the cached JSON value, or None if missing, unreadable or past the namespace TTL."""
    path = CACHE_ROOT / namespace / f"{key}.json.gz"
    if not path.exists():
        return None
    try:
        if _is_stale(path.stat().st_mtime, namespace):
            return None
        return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
    except Exception as e:
        logger.warning(f"read_json_cache failed ({namespace}/{key}): {e}")
        return None
