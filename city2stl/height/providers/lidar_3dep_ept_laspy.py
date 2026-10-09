"""USGS 3DEP lidar from the public Entwine (EPT) octree, read with laspy (no PDAL).

Why a second US route: Microsoft Planetary Computer's COPC collection
(``lidar_3dep_copc``) has gaps over whole downtowns -- none over central Miami or
Seattle (2026-10-03) -- and serves older projects where newer ones exist (Boston:
2013 there, 2021 here). USGS's own octree on ``usgs-lidar-public`` covers every
published project; ``lidar_3dep_ept`` reads it through PDAL, which is a conda-only
install. This reads it with ``laspy[lazrs]``, already a dependency.

How:
  1. Project lookup in the USGS boundary index (``resources.geojson``, cached on disk,
     parsed once per process into an STRtree); the newest project meeting the bbox
     wins (``_project_year``: a measured flight year, else the latest year in the name,
     else the work-unit suffix year such as ``_B20``).
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
import threading
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


#: Flight year of projects whose name does not give it, measured from the GpsTime of one
#: EPT node at the place named (2026-10-08/09). The name rule below ranked the 2023 NOAA
#: topobathy over Waikiki last (no year in its name), so Honolulu read the 2013 survey,
#: which predates the 2017-2018 towers; and the 2016 Puerto Rico flight ranked 2018 (its
#: LAS year), level with the post-Maria 2018 flight, which won on its name only.
PROJECT_YEARS: dict[str, int] = {
    "HI_NOAAMauiOahu_2_B20": 2023,                 # 2023-03-09 at Waikiki
    "USGS_LPC_HI_Oahu_2012_LAS_2015": 2013,        # 2013-06-20 at Waikiki
    "USGS_LPC_PR_PuertoRico_2015_LAS_2018": 2016,  # 2016-03-19 at Condado
    "USGS_LPC_PR_PuertoRico_2016_LAS_2017": 2016,  # 2016-03-19 at Condado
}

_NAME_YEAR = re.compile(r"(?<!\d)(19[89]\d|20[0-4]\d)(?!\d)")
#: USGS work-unit suffix ``_<letter><yy>`` (``AL_19Co_1_B24``, ``HI_NOAAMauiOahu_2_B20``):
#: the fiscal year the work unit was funded, so the flight is that year or later.
_WORK_UNIT_YEAR = re.compile(r"_[A-Z](\d{2})$")


def _project_year(name: str) -> int:
    """Year a project ranks by: ``PROJECT_YEARS`` (measured), else the latest plausible year
    in its name (``IL_4County_Cook_2017_LAS_2019`` -> 2019), else its work-unit suffix
    (``CA_LosAngeles_1_B23`` -> 2023), else 0.

    226 of the index's 230 projects without a four-digit year carry the suffix
    (2026-10-09); the rest are ``*_FullState`` and ``NY_NewYorkCity``.
    """
    if name in PROJECT_YEARS:
        return PROJECT_YEARS[name]
    years = [int(y) for y in _NAME_YEAR.findall(name)]
    if years:
        return max(years)
    m = _WORK_UNIT_YEAR.search(name)
    return 2000 + int(m.group(1)) if m else 0


#: How long the parsed boundary index is reused in one process before ``_resources`` is
#: asked again (it refreshes the file after ``_RESOURCES_TTL_S``).
_INDEX_TTL_S = 86400.0
_INDEX: dict = {}
_INDEX_LOCK = threading.Lock()


def _project_index():
    """``(names, urls, geoms, STRtree, memo)`` over the boundary index, built once per process.

    Rebuilt when ``_resources`` is replaced (tests) or after ``_INDEX_TTL_S``. Parsing the
    2279-feature JSON and rebuilding every shape cost 3.3 s per ``projects_for_bbox`` call,
    and ``benchmark.survey_part`` makes one or two per footprint (1.5 h per 1000).
    ``memo`` maps a bbox to the positions of the projects meeting it.
    """
    with _INDEX_LOCK:  # survey reads run in threads; build the index once, not per thread
        hit = _INDEX.get("index")
        if hit is not None and hit[0] is _resources and time.monotonic() - hit[1] < _INDEX_TTL_S:
            return hit[2]
        index = _build_project_index()
        _INDEX["index"] = (_resources, time.monotonic(), index)
        return index


def _build_project_index():
    import shapely
    from shapely.geometry import shape
    from shapely.strtree import STRtree

    names, urls, geoms = [], [], []
    for feat in _resources().get("features", []):
        props = feat.get("properties") or {}
        name = props.get("name")
        if not name:
            continue
        try:
            geom = shape(feat["geometry"])
        except Exception:
            continue
        names.append(name)
        urls.append(props.get("url") or f"{_BUCKET}/{name}/ept.json")
        geoms.append(geom)
    shapely.prepare(geoms)
    return names, urls, geoms, STRtree(geoms), {}


#: Bboxes remembered per index (a region's footprints, each asked once or twice).
_MEMO_MAX = 50000


def projects_for_bbox(bbox) -> list[dict]:
    """``[{name, url, year}]`` of the EPT projects meeting ``bbox``, newest first
    (ties by name). The spatial part is memoised per bbox; the years are not, so
    ``PROJECT_YEARS`` edits apply at once."""
    from shapely.geometry import box

    from ._survey import as_nsew

    names, urls, geoms, tree, memo = _project_index()
    key = as_nsew(bbox)
    hits = memo.get(key)
    if hits is None:
        n, s, e, w = key
        area = box(w, s, e, n)
        found = []
        for i in sorted(int(i) for i in tree.query(area)):
            try:
                if geoms[i].intersects(area):
                    found.append(i)
            except Exception:
                continue
        if len(memo) >= _MEMO_MAX:
            memo.clear()
        hits = memo[key] = tuple(found)
    out = [{"name": names[i], "url": urls[i], "year": _project_year(names[i])} for i in hits]
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


#: Tries per node read on a dropped connection or a 5xx. One S3 reset used to fail the whole
#: tile (Honolulu survey truth, 2026-10-08: 207 footprints in one tile lost to a single
#: ``ConnectionResetError``).
_NODE_ATTEMPTS = 3
_NODE_BACKOFF_S = 2.0


def _get_node_bytes(url: str) -> bytes:
    for attempt in range(_NODE_ATTEMPTS):
        try:
            resp = requests.get(url, timeout=_TIMEOUT_S)
            if resp.status_code < 500 or attempt + 1 == _NODE_ATTEMPTS:
                resp.raise_for_status()
                return resp.content
        except (requests.ConnectionError, requests.Timeout,
                requests.exceptions.ChunkedEncodingError):
            if attempt + 1 == _NODE_ATTEMPTS:
                raise
        logger.info("3DEP EPT: retrying %s (attempt %d)", url, attempt + 2)
        time.sleep(_NODE_BACKOFF_S * (2 ** attempt))
    raise RuntimeError("unreachable")  # pragma: no cover


def _read_node(base: str, key: str):
    import laspy

    las = laspy.read(io.BytesIO(_get_node_bytes(f"{base}/ept-data/{key}.laz")))
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
