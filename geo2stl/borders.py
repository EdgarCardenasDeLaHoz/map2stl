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
            gdf = gpd.read_file(f"zip://{path}")[["geometry"]]
            _MEMO[kind] = gdf
    return gdf


def borders_grid(north: float, south: float, east: float, west: float,
                 shape: tuple[int, int], *, lines_by_kind: dict | None = None) -> tuple[np.ndarray, dict]:
    """(grid, counts): border lines burned onto a *shape* grid over the bbox, row 0 north.

    Codes: 0 none, 1 state/province line, 2 country border. *lines_by_kind* replaces the
    Natural Earth data (``{"countries": gdf, "states": gdf}``; tests). ``counts`` holds the
    number of line features of each kind inside the box.
    """
    from numpy2stl.raster import burn_polygons

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
        burned = burn_polygons(list(sub.geometry.to_numpy()), (h, w), bounds=(west, south, east, north),
                               values=1.0, mode="max", dtype=np.uint8)
        if code == COUNTRY:
            # The two datasets are generalised separately, so a state line that follows a
            # country border runs a pixel or two beside it: clear state pixels near one.
            import cv2
            near = cv2.dilate((burned > 0).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
            grid[near & (grid == STATE)] = 0
        grid[burned > 0] = code
    return grid, counts
