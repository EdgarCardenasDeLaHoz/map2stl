"""USGS 3DEP lidar from the public Entwine (EPT) octree, read with laspy (no PDAL).

Why a second US route: Microsoft Planetary Computer's COPC collection
(``lidar_3dep_copc``) has gaps over whole downtowns -- none over central Miami or
Seattle (2026-10-03) -- and serves older projects where newer ones exist (Boston:
2013 there, 2021 here). USGS's own octree on ``usgs-lidar-public`` covers every
published project; ``lidar_3dep_ept`` reads it through PDAL, which is a conda-only
install. This reads it with ``laspy[lazrs]``, already a dependency.

How:
  1. Project lookup in the USGS boundary index (``resources.geojson``, cached);
     the newest project (latest year in its name) meeting the bbox wins.
  2. Hierarchy walk from ``0-0-0-0``, loading sub-hierarchy files only where a
     node meets the bbox.
  3. Every node meeting the bbox is read, at every level: an octree level is a
     sample of the survey, not a coarse copy, so one level alone leaves most
     cells empty (``docs/decisions/survey-lidar.md``, 2026-09-04). Levels finer
     than half the requested resolution are skipped: a 1 m cell needs a few
     returns for its maximum, and Miami's 2019 survey put 8 M points in one
     500 m box at quarter-cell depth (63 s).
  4. ``lidar_3dep_copc.grid_points_ndsm`` grids the returns (highest return per
     cell minus interpolated ground), the same as the COPC reader.

Coordinates: USGS EPT is EPSG:3857 with metric Z.
"""

from __future__ import annotations

import io
import json
import logging
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import requests

logger = logging.getLogger(__name__)

_RESOURCES = ("https://raw.githubusercontent.com/hobuinc/usgs-lidar/master/boundaries/"
              "resources.geojson")
_BUCKET = "https://s3-us-west-2.amazonaws.com/usgs-lidar-public"
_RESOURCES_TTL_S = 30 * 86400
_TIMEOUT_S = 60
#: Parallel EPT node reads per tile. 16 drew S3 connection resets on ~15 % of tiles once several
#: tiles were read at a time (survey truth refresh, 2026-10-07); 4 cleared them.
_WORKERS = 4
#: Pad around the bbox so the ground interpolation has returns on every side.
_PAD_M = 15.0
#: LAS classes that are never a surface: low/high noise, withheld overlap.
_NOISE_CLASSES = (7, 18)


def available() -> bool:
    """True when laspy has a LAZ backend."""
    try:
        import laspy
        return bool(laspy.LazBackend.detect_available())
    except Exception:
        return False


def covers(bbox) -> bool:
    """Cheap pre-check: 3DEP is a US programme. The project lookup is the real test."""
    from .lidar_3dep import _is_in_us
    return _is_in_us(bbox)


# --------------------------------------------------------------------------- projects


def _resources_path() -> Path:
    from geo2stl import cache as geo_cache
    return Path(geo_cache.CACHE_ROOT) / "usgs_ept" / "resources.geojson"


def _resources() -> dict:
    """The USGS EPT boundary index, cached for 30 days."""
    path = _resources_path()
    if path.exists() and time.time() - path.stat().st_mtime < _RESOURCES_TTL_S:
        return json.loads(path.read_text(encoding="utf-8"))
    resp = requests.get(_RESOURCES, timeout=_TIMEOUT_S)
    resp.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(resp.text, encoding="utf-8")
    return resp.json()


def _project_year(name: str) -> int:
    """Latest plausible year in a project name (``IL_4County_Cook_2017_LAS_2019`` -> 2019)."""
    years = [int(y) for y in re.findall(r"(?<!\d)(19[89]\d|20[0-4]\d)(?!\d)", name)]
    return max(years, default=0)


def projects_for_bbox(bbox) -> list[dict]:
    """``[{name, url, year}]`` of the EPT projects meeting ``bbox``, newest first."""
    from shapely.geometry import box, shape

    from ._survey import as_nsew

    n, s, e, w = as_nsew(bbox)
    area = box(w, s, e, n)
    out = []
    for feat in _resources().get("features", []):
        props = feat.get("properties") or {}
        name = props.get("name")
        if not name:
            continue
        try:
            if not shape(feat["geometry"]).intersects(area):
                continue
        except Exception:
            continue
        out.append({"name": name, "url": props.get("url") or f"{_BUCKET}/{name}/ept.json",
                    "year": _project_year(name)})
    return sorted(out, key=lambda p: (-p["year"], p["name"]))


# --------------------------------------------------------------------------- octree


def _get_json(url: str) -> dict:
    resp = requests.get(url, timeout=_TIMEOUT_S)
    resp.raise_for_status()
    return resp.json()


def node_bounds(cube, key: str) -> tuple[float, float, float, float]:
    """2-D bounds ``(x0, y0, x1, y1)`` of octree node ``D-X-Y-Z`` in a cube ``bounds``."""
    d, x, y, _z = (int(v) for v in key.split("-"))
    size = (cube[3] - cube[0]) / (2 ** d)
    x0, y0 = cube[0] + x * size, cube[1] + y * size
    return x0, y0, x0 + size, y0 + size


def _meets(a, b) -> bool:
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def nodes_for_bounds(base: str, ept: dict, bounds, max_depth: int,
                     get_json=_get_json) -> list[str]:
    """Keys of every non-empty node meeting native ``bounds`` down to ``max_depth``."""
    cube = ept["bounds"]
    hier = dict(get_json(f"{base}/ept-hierarchy/0-0-0-0.json"))
    keys, frontier = [], ["0-0-0-0"]
    while frontier:
        nxt = []
        for key in frontier:
            if key not in hier or not _meets(node_bounds(cube, key), bounds):
                continue
            if hier[key] == -1:  # this subtree's counts live in their own file
                hier.update(get_json(f"{base}/ept-hierarchy/{key}.json"))
            if hier.get(key, 0) > 0:
                keys.append(key)
            d, x, y, z = (int(v) for v in key.split("-"))
            if d < max_depth:
                nxt += [f"{d + 1}-{2 * x + i}-{2 * y + j}-{2 * z + k}"
                        for i in (0, 1) for j in (0, 1) for k in (0, 1)]
        frontier = nxt
    return keys


def _read_node(base: str, key: str):
    import laspy

    resp = requests.get(f"{base}/ept-data/{key}.laz", timeout=_TIMEOUT_S)
    resp.raise_for_status()
    las = laspy.read(io.BytesIO(resp.content))
    cls = np.asarray(las.classification)
    keep = ~np.isin(cls, _NOISE_CLASSES)
    return (np.asarray(las.x)[keep], np.asarray(las.y)[keep], np.asarray(las.z)[keep],
            cls[keep])


# --------------------------------------------------------------------------- nDSM


def ndsm_for_bbox(bbox, resolution_m: float = 1.0, project: str | None = None):
    """Height above ground over ``bbox`` from the newest EPT project (or ``project``).

    ``(array row0=north, lon/lat Affine)`` on the survey grid, or None when laspy is
    missing, the bbox is outside the US, or no project covers it. Cached per project.
    """
    from ._survey import as_nsew, cached_ndsm

    if not covers(bbox) or not available():
        return None
    nsew = as_nsew(bbox)
    if project is None:
        found = projects_for_bbox(nsew)
        if not found:
            return None
        project = found[0]["name"]
    return cached_ndsm(f"survey_usgs_3dep_ept_{project}", nsew, resolution_m,
                       lambda: _grid(nsew, resolution_m, project))


def _grid(bbox, res: float, project: str):
    from pyproj import Transformer

    from ._survey import SurveyError
    from .lidar_3dep_copc import grid_points_ndsm

    n, s, e, w = bbox
    base = f"{_BUCKET}/{project}"
    t0 = time.perf_counter()
    try:
        ept = _get_json(f"{base}/ept.json")
    except requests.RequestException as exc:
        raise SurveyError(f"3DEP EPT {project}: ept.json failed ({exc})") from exc
    srs = ept.get("srs") or {}
    crs = f"EPSG:{srs['horizontal']}" if srs.get("horizontal") else srs.get("wkt")
    to_native = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    to_ll = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    xs, ys = to_native.transform([w, e, w, e], [s, s, n, n])
    # Web-Mercator metres are ground metres x 1/cos(lat).
    k = 1.0 / math.cos(math.radians((n + s) / 2)) if "3857" in str(crs) else 1.0
    pad = _PAD_M * k
    bounds = (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)
    cube_w = ept["bounds"][3] - ept["bounds"][0]
    root_spacing = cube_w / ept["span"]
    max_depth = max(0, math.ceil(math.log2(root_spacing / (res * k / 2))))
    try:
        keys = nodes_for_bounds(base, ept, bounds, max_depth)
    except requests.RequestException as exc:
        raise SurveyError(f"3DEP EPT {project}: hierarchy failed ({exc})") from exc
    if not keys:
        return None
    try:
        with ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="ept") as pool:
            parts = list(pool.map(lambda key: _read_node(base, key), keys))
    except Exception as exc:
        raise SurveyError(f"3DEP EPT {project}: node read failed ({exc})") from exc
    chunks, n_pts = [], 0
    for x, y, z, cls in parts:
        m = (x >= bounds[0]) & (x <= bounds[2]) & (y >= bounds[1]) & (y <= bounds[3])
        if not m.any():
            continue
        lon, lat = to_ll.transform(x[m], y[m])
        chunks.append((np.asarray(lon), np.asarray(lat), z[m], cls[m]))
        n_pts += int(m.sum())
    logger.info("3DEP EPT %s: %d nodes, %d points in bbox, %.1fs", project, len(keys), n_pts,
                time.perf_counter() - t0)
    return grid_points_ndsm(chunks, bbox, res) if chunks else None
