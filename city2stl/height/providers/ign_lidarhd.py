"""France: IGN LiDAR HD height above ground (MNH, "modèle numérique de hauteur").

IGN publishes the LiDAR HD MNH (0.5 m, height of the surface above the ground, so
no DSM - DTM subtraction) through its open WMS-R at ``data.geopf.fr/wms-r``. The
``...WGS84G`` layer answers ``FORMAT=image/geotiff`` with **one float32 band of
metres** over any window (nodata -9999) -- checked 2026-09-27 over Notre-Dame de
Paris (0-73 m). No key, no account.

Coverage: LiDAR HD is being flown department by department over metropolitan
France (and Réunion); the extent check below is metropolitan France, and a bbox
the survey has not reached yet comes back all nodata, which is reported as no
coverage (``None``).
"""

from __future__ import annotations

import logging

from ._survey import (
    SurveyError,
    as_nsew,
    cached_ndsm,
    clean_heights,
    http_get,
    intersects,
    lonlat_grid,
    read_geotiff_array,
)

logger = logging.getLogger(__name__)

name = "ign_lidarhd"
label = "IGN LiDAR HD MNH (France, 0.5 m)"
resolution_m = 0.5

WMS_URL = "https://data.geopf.fr/wms-r"
LAYER = "IGNF_LIDAR-HD_MNH_ELEVATION.ELEVATIONGRIDCOVERAGE.WGS84G"
#: Metropolitan France incl. Corsica (n, s, e, w).
EXTENTS = [(51.2, 41.3, 9.7, -5.3)]


def covers(bbox) -> bool:
    return intersects(bbox, EXTENTS)


def _fetch(bbox, res: float):
    n, s, e, w = as_nsew(bbox)
    h, wd, transform = lonlat_grid(bbox, res)
    params = {
        "SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetMap", "LAYERS": LAYER,
        "STYLES": "", "CRS": "EPSG:4326",
        # WMS 1.3.0 + EPSG:4326 is latitude-first.
        "BBOX": f"{s},{w},{n},{e}", "WIDTH": wd, "HEIGHT": h, "FORMAT": "image/geotiff",
    }
    arr, _t, _crs, nodata = read_geotiff_array(http_get(WMS_URL, params, "IGN LiDAR HD"),
                                               "IGN LiDAR HD")
    if arr.shape != (h, wd):
        raise SurveyError(f"IGN LiDAR HD: asked for {wd}x{h}, got {arr.shape[1]}x{arr.shape[0]}")
    if nodata is not None:
        arr[arr == nodata] = float("nan")
    return clean_heights(arr), transform


def ndsm_for_bbox(bbox, resolution_m: float = resolution_m):
    """MNH over ``bbox`` on a lon/lat grid (see ``_survey``), or None outside coverage."""
    if not covers(bbox):
        return None
    return cached_ndsm(f"survey_{name}", bbox, resolution_m, lambda: _fetch(bbox, resolution_m))
