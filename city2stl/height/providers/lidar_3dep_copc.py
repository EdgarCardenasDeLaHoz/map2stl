"""
USGS 3DEP lidar building heights, measured per footprint from the point cloud.

Not a raster ``HeightProvider``. Every other US source reaching the city
enhancement was a 30 m difference of two global DEMs (``lidar_3dep`` is COP30
minus SRTM, despite its name), which the resolution limit rightly refuses: a
30 m cell measures the block, not the roof. OpenTopography cannot supply
anything finer for the US -- its 3DEP API serves bare-earth DTMs only, and the
1 m one is restricted to academic accounts. The building heights are in the
point clouds themselves, which Microsoft Planetary Computer publishes as COPC
(cloud-optimised LAZ) files that can be read by HTTP range, no key required.

Per building (``footprint_heights``):
  roof   = median of the non-ground returns inside the footprint
  ground = median of the ground-classified (class 2) returns in a ring
           ``_GROUND_RING_M`` around it
  height = roof - ground

Measured inside the footprint rather than on a grid, so a house under
lodgepole pines is not given the trees' height the way a gridded maximum would,
and no ``building`` class is needed (most 3DEP projects, Breckenridge's
included, classify only ground and "unclassified").

``ndsm_for_bbox`` grids the same point cloud (highest return minus the
interpolated ground) for the survey-provider interface of
``city2stl.height.providers._survey`` -- a landmark's nDSM override.

A building with too few returns keeps its fallback height for the raster
providers to fill; so does one that measures under ``_MIN_HEIGHT_M``, which is
usually a building that went up after the survey.

Requires ``laspy[lazrs]``; without it :func:`available` is False and US cities
use the raster providers alone.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests

logger = logging.getLogger(__name__)

_STAC_SEARCH = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
_SAS_TOKEN = "https://planetarycomputer.microsoft.com/api/sas/v1/token/{account}/{container}"
_COLLECTION = "3dep-lidar-copc"
_TIMEOUT_S = 60

#: Point spacing requested from the COPC octree, metres. 3DEP QL2 is ~0.7 m;
#: reading the 2 m levels is ~4x less data and still puts dozens of returns on
#: a house roof.
_QUERY_SPACING_M = 2.0
_GROUND_RING_M = 8.0
#: The median roof return, i.e. mid-roof: the height of a flat-topped box with
#: the roof's volume. Against Breckenridge's 185 OSM-tagged buildings the
#: median scores MAE 1.98 m / corr +0.876, the 75th 2.74 / +0.838 and the 90th
#: 3.51 / +0.808 (steep snow roofs put the upper returns well above the eaves);
#: GBA + Open Buildings on the same buildings score 2.13 / +0.666.
_ROOF_PERCENTILE = 50.0
_MIN_ROOF_POINTS = 4
_MIN_GROUND_POINTS = 3
_MIN_HEIGHT_M = 2.0
_MAX_HEIGHT_M = 300.0
#: Tiles read at once. Each reader also runs its own HTTP range threads.
_MAX_TILE_WORKERS = 6
_HTTP_THREADS_PER_TILE = 8
#: LAS classes that are never a surface: low/high noise, withheld overlap.
_NOISE_CLASSES = (7, 18)


def available() -> bool:
    """True when the COPC reader (laspy with a LAZ backend) is installed."""
    try:
        import laspy
        return bool(laspy.LazBackend.detect_available())
    except Exception:
        return False


def _is_in_us(bbox) -> bool:
    from .lidar_3dep import _is_in_us as in_us
    return in_us(bbox)


def covers(bbox) -> bool:
    """Cheap pre-check: 3DEP is a US programme. The STAC search is the real test."""
    return _is_in_us(bbox)


def _search_items(bbox) -> list[dict]:
    """Every COPC tile intersecting *bbox* = (north, south, east, west)."""
    north, south, east, west = bbox
    body: dict | None = {"collections": [_COLLECTION], "bbox": [west, south, east, north],
                         "limit": 250}
    url = _STAC_SEARCH
    items: list[dict] = []
    while body is not None:
        resp = requests.post(url, json=body, timeout=_TIMEOUT_S)
        resp.raise_for_status()
        page = resp.json()
        items.extend(page.get("features") or [])
        nxt = next((ln for ln in page.get("links") or [] if ln.get("rel") == "next"), None)
        if nxt is None:
            break
        url, body = nxt["href"], nxt.get("body")
    return items


def _signed(href: str, tokens: dict[str, str]) -> str:
    """Append a Planetary Computer SAS token for the blob container behind *href*."""
    # https://<account>.blob.core.windows.net/<container>/...
    host, _, path = href.split("://", 1)[1].partition("/")
    account, container = host.split(".", 1)[0], path.split("/", 1)[0]
    key = f"{account}/{container}"
    if key not in tokens:
        resp = requests.get(_SAS_TOKEN.format(account=account, container=container),
                            timeout=_TIMEOUT_S)
        resp.raise_for_status()
        tokens[key] = resp.json()["token"]
    return f"{href}?{tokens[key]}"


def _item_crs(item: dict):
    """(horizontal CRS, horizontal unit in m, vertical unit in m) of a COPC item."""
    from pyproj import CRS

    crs = CRS.from_json_dict(item["properties"]["proj:projjson"])
    horiz, vert = crs, None
    if crs.is_compound:
        horiz, vert = crs.sub_crs_list[0], crs.sub_crs_list[1]
    h_unit = horiz.axis_info[0].unit_conversion_factor if horiz.axis_info else 1.0
    v_unit = vert.axis_info[0].unit_conversion_factor if vert is not None and vert.axis_info else h_unit
    return horiz, float(h_unit or 1.0), float(v_unit or 1.0)


def _query_points(item: dict, bounds, spacing: float, v_unit: float, tokens: dict[str, str]):
    """``(x, y, z metres, class)`` of one tile's returns inside native ``bounds``
    (x0, y0, x1, y1) at octree ``spacing`` (native units), noise classes dropped."""
    import laspy

    x0, y0, x1, y1 = bounds
    href = _signed(item["assets"]["data"]["href"], tokens)
    with laspy.CopcReader.open(href, http_num_threads=_HTTP_THREADS_PER_TILE) as reader:
        pts = reader.query(
            bounds=laspy.copc.Bounds(mins=np.array([x0, y0]), maxs=np.array([x1, y1])),
            resolution=spacing,
        )
    x, y = np.asarray(pts.x), np.asarray(pts.y)
    z = np.asarray(pts.z) * v_unit
    cls = np.asarray(pts.classification)
    keep = ~np.isin(cls, _NOISE_CLASSES)
    return x[keep], y[keep], z[keep], cls[keep]


def _read_tile(item: dict, polys_ll: dict[int, object], tokens: dict[str, str]):
    """Roof and ground elevations (metres) per building index, from one tile."""
    import shapely
    from pyproj import Transformer

    horiz, h_unit, v_unit = _item_crs(item)
    to_native = Transformer.from_crs("EPSG:4326", horiz, always_xy=True)
    ring = _GROUND_RING_M / h_unit
    native = {i: shapely.transform(p, lambda xy: np.column_stack(to_native.transform(xy[:, 0], xy[:, 1])))
              for i, p in polys_ll.items()}
    outer = {i: p.buffer(ring) for i, p in native.items()}
    x0, y0, x1, y1 = shapely.total_bounds(list(outer.values()))

    x, y, z, cls = _query_points(item, (x0, y0, x1, y1), _QUERY_SPACING_M / h_unit, v_unit, tokens)
    order = np.argsort(x)
    x, y, z, cls = x[order], y[order], z[order], cls[order]

    out: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    for i, poly in native.items():
        bx0, by0, bx1, by1 = outer[i].bounds
        lo, hi = np.searchsorted(x, [bx0, bx1])
        sx, sy, sz, sc = x[lo:hi], y[lo:hi], z[lo:hi], cls[lo:hi]
        near = (sy >= by0) & (sy <= by1)
        sx, sy, sz, sc = sx[near], sy[near], sz[near], sc[near]
        if not len(sx):
            continue
        inside = shapely.contains_xy(poly, sx, sy)
        ground = (sc == 2) & ~inside & shapely.contains_xy(outer[i], sx, sy)
        roof = inside & (sc != 2)
        if roof.any() or ground.any():
            out[i] = (sz[roof], sz[ground])
    return out, len(x)


def footprint_heights(polygons: dict[int, object], bbox) -> tuple[dict[int, float], dict]:
    """Measure building heights from 3DEP lidar.

    Args:
        polygons: building index -> shapely Polygon/MultiPolygon in lon/lat.
        bbox: (north, south, east, west) of the request.

    Returns:
        ``({index: height_m}, stats)``. Buildings the lidar cannot measure are
        absent from the dict. ``stats["tiles"] == 0`` means no 3DEP point cloud
        covers the bbox.
    """
    t0 = time.perf_counter()
    stats = {"tiles": 0, "tiles_failed": 0, "points": 0, "measured": 0,
             "too_few_points": 0, "below_min_height": 0}
    if not polygons:
        return {}, stats
    items = _search_items(bbox)

    import shapely
    ids = list(polygons)
    tree = shapely.STRtree([polygons[i] for i in ids])
    jobs = []
    for item in items:
        ib = item.get("bbox") or []
        if len(ib) == 6:
            ib = [ib[0], ib[1], ib[3], ib[4]]
        if len(ib) != 4:
            continue
        hits = tree.query(shapely.box(*ib))
        if len(hits):
            jobs.append((item, {ids[k]: polygons[ids[k]] for k in hits}))
    stats["tiles"] = len(jobs)
    if not jobs:
        stats["seconds"] = round(time.perf_counter() - t0, 1)
        return {}, stats

    tokens: dict[str, str] = {}
    _signed(jobs[0][0]["assets"]["data"]["href"], tokens)   # fetch the token once, up front
    roofs: dict[int, list[np.ndarray]] = {}
    grounds: dict[int, list[np.ndarray]] = {}
    with ThreadPoolExecutor(max_workers=_MAX_TILE_WORKERS, thread_name_prefix="3dep") as pool:
        futures = [pool.submit(_read_tile, item, polys, tokens) for item, polys in jobs]
        for (item, _polys), fut in zip(jobs, futures, strict=True):
            try:
                per_building, n_points = fut.result()
            except Exception as exc:
                stats["tiles_failed"] += 1
                logger.warning("3DEP lidar tile %s failed: %s", item.get("id"), exc)
                continue
            stats["points"] += n_points
            for i, (roof, ground) in per_building.items():
                roofs.setdefault(i, []).append(roof)
                grounds.setdefault(i, []).append(ground)

    heights: dict[int, float] = {}
    for i in polygons:
        roof = np.concatenate(roofs.get(i, [np.empty(0)]))
        ground = np.concatenate(grounds.get(i, [np.empty(0)]))
        if len(roof) < _MIN_ROOF_POINTS or len(ground) < _MIN_GROUND_POINTS:
            stats["too_few_points"] += 1
            continue
        h = float(np.percentile(roof, _ROOF_PERCENTILE) - np.median(ground))
        if h < _MIN_HEIGHT_M:
            stats["below_min_height"] += 1
            continue
        heights[i] = round(min(h, _MAX_HEIGHT_M), 1)
    stats["measured"] = len(heights)
    stats["seconds"] = round(time.perf_counter() - t0, 1)
    logger.info("3DEP lidar: %d/%d buildings measured from %d tile(s) (%d failed), "
                "%d points, %.1fs", len(heights), len(polygons), stats["tiles"],
                stats["tiles_failed"], stats["points"], stats["seconds"])
    return heights, stats


# ---------------------------------------------------------------------------
# Gridded nDSM (F-LANDMARK §4: the survey-provider interface)
# ---------------------------------------------------------------------------

#: Ground returns are interpolated from this far around the bbox, so a landmark
#: whose footprint fills the window still has ground on every side.
_GRID_PAD_M = 15.0


def ndsm_for_bbox(bbox, resolution_m: float = 1.0):
    """Height above ground over ``bbox`` from the COPC point cloud, on the survey grid.

    Surface = highest return per cell; ground = the class-2 returns interpolated
    (linear, nearest outside their hull) to the cell centres. Cells no return
    reached are NaN. ``(array row0=north, lon/lat Affine)`` or None when laspy is
    missing, the bbox is outside the US, or no 3DEP tile covers it. See
    ``city2stl.height.providers._survey`` for the contract.
    """
    from ._survey import cached_ndsm

    if not covers(bbox) or not available():
        return None
    return cached_ndsm("survey_usgs_3dep_copc", bbox, resolution_m,
                       lambda: _grid_ndsm(bbox, resolution_m))


def _grid_ndsm(bbox, res: float):
    from pyproj import Transformer

    from ._survey import SurveyError, as_nsew

    n, s, e, w = as_nsew(bbox)
    try:
        items = _search_items((n, s, e, w))
    except requests.RequestException as exc:
        raise SurveyError(f"3DEP COPC: STAC search failed ({exc})") from exc
    if not items:
        return None
    chunks = []
    tokens: dict[str, str] = {}
    for item in items:
        horiz, h_unit, v_unit = _item_crs(item)
        to_native = Transformer.from_crs("EPSG:4326", horiz, always_xy=True)
        to_ll = Transformer.from_crs(horiz, "EPSG:4326", always_xy=True)
        xs, ys = to_native.transform([w, e, w, e], [s, s, n, n])
        pad = _GRID_PAD_M / h_unit
        bounds = (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)
        try:
            x, y, z, cls = _query_points(item, bounds, res / h_unit, v_unit, tokens)
        except Exception as exc:
            raise SurveyError(f"3DEP COPC tile {item.get('id')}: {exc}") from exc
        lon, lat = to_ll.transform(x, y)
        chunks.append((np.asarray(lon), np.asarray(lat), z, cls))
    return grid_points_ndsm(chunks, (n, s, e, w), res)


def grid_points_ndsm(chunks, bbox, res: float, max_height_m: float = _MAX_HEIGHT_M):
    """Grid lidar returns into height above ground on the survey lon/lat grid.

    ``chunks``: iterable of ``(lon, lat, z metres, LAS class)`` arrays. Surface = highest
    return per cell; ground = the class-2 returns interpolated (linear, nearest outside
    their hull) to the cell centres. Shared by the COPC reader and the laspy EPT reader
    (``lidar_3dep_ept_laspy``). ``(array row0=north, Affine)``, or None when there is no
    ground or no return lands in the bbox.
    """
    from scipy.interpolate import griddata

    from ._survey import as_nsew, clean_heights, lonlat_grid

    n, s, e, w = as_nsew(bbox)
    h, wd, transform = lonlat_grid(bbox, res)
    dsm = np.full((h, wd), -np.inf)
    ground: list[np.ndarray] = []
    for lon, lat, z, cls in chunks:
        g = cls == 2
        if g.any():
            ground.append(np.column_stack([lon[g], lat[g], z[g]]))
        col = np.floor((lon - w) / transform.a).astype(np.int64)
        row = np.floor((lat - n) / transform.e).astype(np.int64)
        inside = (col >= 0) & (col < wd) & (row >= 0) & (row < h)
        np.maximum.at(dsm, (row[inside], col[inside]), z[inside])
    if not ground or not np.isfinite(dsm).any():
        return None
    gp = np.concatenate(ground)
    cols, rows = np.meshgrid(np.arange(wd) + 0.5, np.arange(h) + 0.5)
    cx, cy = transform * (cols, rows)
    dtm = griddata(gp[:, :2], gp[:, 2], (cx, cy), method="linear")
    if np.isnan(dtm).any():
        near = griddata(gp[:, :2], gp[:, 2], (cx, cy), method="nearest")
        dtm = np.where(np.isnan(dtm), near, dtm)
    out = np.where(np.isfinite(dsm), dsm - dtm, np.nan)
    return clean_heights(out, hi=max_height_m), transform
