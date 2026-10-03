"""Disk caches for the city model's derived geometry (``geo2stl.cache`` primitives).

A city build spends most of its time turning the same OSM features into the same
geometry. Three caches, each keyed by a digest of *everything* the result depends
on, so a hit is exactly what a rebuild would produce:

* ``city_polygons`` (JSON): one layer's features as clipped, simplified, snapped
  model-mm polygons with their properties and counts (``feature_polygons``).
  Key: layer, feature digest, bbox, exact scale, DEM shape, the style fields and
  module constants the polygons depend on. Shared by the build and the pre-flight.
* ``city_solids`` (npz, float64 vertices): one layer's finished union solid, plus
  its report block. Key: the polygon key, the whole style, a digest of the
  terrain heightfield (skirts and slabs follow it) and of the landmark overrides
  that affect the layer (buildings: the overrides themselves; later extrude
  layers: the footprints the overrides replaced; other layers: none).
* ``city_terrain`` (npz): the adaptive terrain solid. Key: digest of the
  heightfield, tolerance, mm_per_px.
* ``city_models`` (npz, written by ``city_model.build_on_terrain``): the finished
  merged solid and parts after assembly and lossless simplification. Key: the
  terrain, every built layer's solid key, and the simplify flag - so an
  unchanged rebuild skips the booleans and the simplification as well.

``MODEL_CACHE_VERSION`` is part of every key: bump it whenever code changes what a
layer or the terrain looks like (city_model, roofs, landmarks, numpy2stl's prism /
TIN), so stale geometry is never served. Set ``MAP2STL_CITY_CACHE=0`` to disable.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import asdict

import numpy as np
import shapely

from geo2stl.cache import read_array_cache, read_json_cache, write_array_cache, write_json_cache

logger = logging.getLogger(__name__)

#: Part of every key. Bump when geometry code changes (see the module docstring).
MODEL_CACHE_VERSION = 4   # 4: buildings widened after the flat-roof merge, 2026-10-02
# 3: tiled terrain TIN (numpy2stl heightfield_tin), 2026-09-29

NS_POLYGONS = "city_polygons"
NS_SOLIDS = "city_solids"
NS_TERRAIN = "city_terrain"


def enabled() -> bool:
    """False when ``MAP2STL_CITY_CACHE`` is 0 / false / off."""
    return os.environ.get("MAP2STL_CITY_CACHE", "1").strip().lower() not in ("0", "false", "off", "no")


def digest(*parts) -> str:
    """sha1 over arrays (dtype, shape, bytes), bytes, and JSON-able values."""
    h = hashlib.sha1()
    for p in parts:
        if isinstance(p, np.ndarray):
            a = np.ascontiguousarray(p)
            h.update(f"{a.dtype}{a.shape}".encode())
            h.update(a.tobytes())
        elif isinstance(p, bytes | bytearray):
            h.update(p)
        else:
            h.update(json.dumps(p, sort_keys=True, default=str, separators=(",", ":")).encode())
        h.update(b"\x00")
    return h.hexdigest()


def terrain_key(z: np.ndarray, max_error: float, mm_per_px: float) -> str:
    return digest(MODEL_CACHE_VERSION, "terrain", np.asarray(z, np.float64), float(max_error),
                  float(mm_per_px))


def polygons_key(name: str, features_digest: str, bbox: dict, scale, dem_shape, style,
                 constants: dict) -> str:
    """Key of a layer's polygons: only what ``feature_polygons`` reads."""
    return digest(MODEL_CACHE_VERSION, "polygons", name, features_digest,
                  {k: float(bbox[k]) for k in ("north", "south", "east", "west")},
                  asdict(scale), list(dem_shape),
                  {"line_width_m": style.line_width_m, "min_width_mm": style.min_width_mm},
                  constants)


def solid_key(polys_key: str, style, terrain_digest: str, landmarks_digest: str | None) -> str:
    return digest(MODEL_CACHE_VERSION, "solid", polys_key, asdict(style), terrain_digest,
                  landmarks_digest)


def overrides_digest(overrides: dict) -> str:
    """Digest of resolved landmark overrides (``LandmarkOverride`` by osm_id)."""
    parts = []
    for oid in sorted(overrides):
        ov = overrides[oid]
        mesh = getattr(ov, "mesh", None)
        parts += [oid, ov.kind, ov.spec, ov.source,
                  np.asarray(mesh.vertices) if mesh is not None else None,
                  np.asarray(mesh.faces) if mesh is not None else None,
                  np.asarray(ov.ndsm) if ov.ndsm is not None else None,
                  tuple(ov.transform) if ov.transform is not None else None]
    return digest(*parts)


def footprints_digest(footprints: dict) -> str:
    return digest(*[(oid, shapely.to_wkb(footprints[oid], hex=True)) for oid in sorted(footprints)])


# ---------------------------------------------------------------------------
# Polygons (JSON)
# ---------------------------------------------------------------------------

def read_polygons(key: str) -> tuple[list, list[dict], dict] | None:
    got = read_json_cache(NS_POLYGONS, key)
    if not isinstance(got, dict):
        return None
    try:
        polys = shapely.from_wkb(got["wkb"]) if got["wkb"] else np.zeros(0, object)
        # WKB drops the precision grid set_precision gave the polygons; GEOS
        # honours it in later overlays (noding, buffers), so it is restored
        # (pointwise: coordinates are already on it and do not move).
        grid = np.asarray(got.get("grid") or np.zeros(len(polys)), np.float64)
        if len(polys) and (grid > 0).any():
            polys = shapely.set_precision(polys, grid, mode="pointwise")
        return list(polys), got["props"], got["counts"]
    except Exception as exc:   # a corrupt entry is a miss
        logger.warning("city polygon cache %s unreadable: %s", key[:8], exc)
        return None


def write_polygons(key: str, polys: list, props: list[dict], counts: dict) -> None:
    arr = np.asarray(polys, dtype=object)
    wkb = shapely.to_wkb(arr, hex=True).tolist() if polys else []
    grid = shapely.get_precision(arr).tolist() if polys else []
    write_json_cache(NS_POLYGONS, key, {"wkb": wkb, "grid": grid, "props": props, "counts": counts})


# ---------------------------------------------------------------------------
# Meshes (npz, exact dtypes) with a JSON metadata sidecar
# ---------------------------------------------------------------------------

def read_mesh(namespace: str, key: str) -> tuple[np.ndarray, np.ndarray, dict] | None:
    got = read_array_cache(namespace, key)
    if got is None:
        return None
    arrays, meta = got
    if "v" not in arrays or "f" not in arrays:
        return None
    return arrays["v"].astype(np.float64), arrays["f"].astype(np.int64), meta


def write_mesh(namespace: str, key: str, v: np.ndarray, f: np.ndarray, meta: dict | None = None) -> None:
    write_array_cache(namespace, key,
                      {"v": np.asarray(v, np.float64).reshape(-1, 3),
                       "f": np.asarray(f, np.int64).reshape(-1, 3)},
                      meta or {}, keep_dtype=True)
