"""Country and state/province borders as a grid, for the Edit map's Borders layer (view only).

Natural Earth 10 m boundary lines: ``admin_0_boundary_lines_land`` (land borders between
countries; coastlines are left to the terrain) and ``admin_1_states_provinces_lines``
(first-level subdivisions). Each archive is downloaded once to ``cache/natural_earth/`` and
held in memory afterwards, like the Natural Earth rivers (``water_layers.py``).

:func:`borders_grid` burns them onto a grid over the bbox (row 0 north): 0 = none,
1 = state/province line, 2 = country border (a country border wins where both run).
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

#: Natural Earth archives per border kind (``cultural`` theme, 10 m scale).
NE_FILES = {
    "countries": "ne_10m_admin_0_boundary_lines_land.zip",
    "states": "ne_10m_admin_1_states_provinces_lines.zip",
}
#: Grid codes.
STATE, COUNTRY = 1, 2

_MEMO: dict = {}
#: Per kind, each line's GeoJSON mapping (None until first burnt), by position in
#: :func:`border_lines`. Converting to GeoJSON was 0.47 of the 0.55 s per grid miss.
_GEOJSON: dict = {}
_LOCK = threading.Lock()


def _cache_dir() -> Path:
    from geo2stl.hydrology import _MAP2STL_ROOT
    return Path(_MAP2STL_ROOT) / "cache" / "natural_earth"


def border_lines(kind: str):
    """All Natural Earth border lines of *kind* ('countries' or 'states') as a GeoDataFrame."""
    import geopandas as gpd

    with _LOCK:                     # one download, even when two requests ask at once
        gdf = _MEMO.get(kind)
        if gdf is None:
            name = NE_FILES[kind]
            path = _cache_dir() / name
            if not path.exists():
                import requests
                path.parent.mkdir(parents=True, exist_ok=True)
                url = f"https://naciscdn.org/naturalearth/10m/cultural/{name}"
                logger.info("Downloading Natural Earth borders: %s", url)
                resp = requests.get(url, timeout=120)
                resp.raise_for_status()
                tmp = path.with_suffix(".part")
                tmp.write_bytes(resp.content)
                tmp.replace(path)
            gdf = gpd.read_file(f"zip://{path}")[["geometry"]].reset_index(drop=True)
            _MEMO[kind] = gdf
            _GEOJSON[kind] = np.full(len(gdf), None, dtype=object)
    return gdf


def _to_mappings(geoms) -> list:
    """GeoJSON mappings of shapely geometries, converted in one GEOS call."""
    import shapely
    try:
        from orjson import loads
    except ImportError:
        from json import loads
    return [loads(s) for s in shapely.to_geojson(np.asarray(geoms, dtype=object))]


def _line_mappings(kind: str, gdf, sub) -> list:
    """GeoJSON mappings of the non-empty lines in *sub* (a ``.cx`` slice of *gdf*), in order.

    Lines of the Natural Earth memo (*gdf* is ``border_lines(kind)``) are converted once
    and kept in ``_GEOJSON``; any other frame (tests) is converted every call.
    """
    import shapely
    geoms = sub.geometry.to_numpy()
    ok = shapely.is_geometry(geoms)
    ok[ok] = ~shapely.is_empty(geoms[ok])
    memo = _GEOJSON.get(kind) if gdf is _MEMO.get(kind) else None
    if memo is None:
        return _to_mappings(geoms[ok])
    pos = sub.index.to_numpy()[ok]          # border_lines resets the index to positions
    with _LOCK:
        missing = pos[[memo[p] is None for p in pos]]
        if len(missing):
            for p, m in zip(missing, _to_mappings(gdf.geometry.to_numpy()[missing]), strict=True):
                memo[p] = m
        return memo[pos].tolist()


def borders_grid(north: float, south: float, east: float, west: float,
                 shape: tuple[int, int], *, lines_by_kind: dict | None = None) -> tuple[np.ndarray, dict]:
    """(grid, counts): border lines burned onto a *shape* grid over the bbox, row 0 north.

    Codes: 0 none, 1 state/province line, 2 country border. *lines_by_kind* replaces the
    Natural Earth data (``{"countries": gdf, "states": gdf}``; tests). ``counts`` holds the
    number of line features of each kind inside the box.
    """
    h, w = int(shape[0]), int(shape[1])
    grid = np.zeros((h, w), dtype=np.uint8)
    counts = {}
    for kind, code in (("states", STATE), ("countries", COUNTRY)):   # countries drawn last win
        gdf = (lines_by_kind or {}).get(kind) if lines_by_kind is not None else border_lines(kind)
        if gdf is None or len(gdf) == 0:
            counts[kind] = 0
            continue
        sub = gdf.cx[west:east, south:north]
        counts[kind] = int(len(sub))
        if not len(sub):
            continue
        burned = _burn_lines(_line_mappings(kind, gdf, sub), (h, w), (west, south, east, north))
        if code == COUNTRY:
            # The two datasets are generalised separately, so a state line that follows a
            # country border runs a pixel or two beside it: clear state pixels near one.
            import cv2
            near = cv2.dilate((burned > 0).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
            grid[near & (grid == STATE)] = 0
        grid[burned > 0] = code
    return grid, counts


def _burn_lines(mappings: list, shape: tuple[int, int], bounds) -> np.ndarray:
    """uint8 grid over *bounds*, 1 on the cells the GeoJSON lines in *mappings* run through.

    ``rasterio.features.rasterize`` on the memoised mappings: the same cells as
    ``numpy2stl.raster.burn_polygons(..., values=1.0, mode="max")``, without its
    per-call GeoJSON conversion. Falls back to it when rasterio is missing.
    """
    h, w = shape
    west, south, east, north = (float(v) for v in bounds)
    if not mappings:
        return np.zeros((h, w), dtype=np.uint8)
    try:
        from affine import Affine
        from rasterio.features import rasterize
    except ImportError:
        from numpy2stl.raster import burn_polygons
        return burn_polygons(mappings, (h, w), bounds=(west, south, east, north),
                             values=1.0, mode="max", dtype=np.uint8)
    aff = Affine((east - west) / w, 0.0, west, 0.0, -(north - south) / h, north)
    return rasterize([(m, 1) for m in mappings], out_shape=(h, w), transform=aff,
                     fill=0, all_touched=False, dtype=np.uint8)
