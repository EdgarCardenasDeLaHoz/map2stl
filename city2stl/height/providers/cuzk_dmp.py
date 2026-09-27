"""Czech Republic: ČÚZK surface (DMP 1G) minus bare earth (DMR 5G).

ČÚZK serves both lidar models as ArcGIS image services
(``ags.cuzk.gov.cz/arcgis2/rest/services/{dmp1g,dmr5g}/ImageServer``). Their
``exportImage`` operation reprojects server-side and returns a float32 GeoTIFF
of metres for any window (``bboxSR=imageSR=4326``, ``pixelType=F32``) -- checked
2026-09-27 over St. Vitus, Prague (surface 222-339 m, ground 218-259 m). No key.

DMP 1G is an irregular (TIN) surface model from the 2009-2013 flights including
buildings **and vegetation** (stated accuracy 0.4 m on buildings, 0.7 m on
canopy); DMR 5G is the ground from the same flights. Clipped to a building
footprint the difference is the roof, but a tree overhanging the footprint
shows up too. Served at any size up to 15000 x 4100 px.

Coverage: the Czech Republic (extent check below; outside the national mosaic
the service answers nodata/0, reported as no coverage).
"""

from __future__ import annotations

import logging

import numpy as np

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

name = "cuzk_dmp"
label = "ČÚZK DMP 1G − DMR 5G (Czechia, ~1 m)"
resolution_m = 1.0

SERVICE_URL = "https://ags.cuzk.gov.cz/arcgis2/rest/services/{service}/ImageServer/exportImage"
SURFACE, GROUND = "dmp1g", "dmr5g"
#: Czech Republic (n, s, e, w).
EXTENTS = [(51.06, 48.55, 18.86, 12.09)]


def covers(bbox) -> bool:
    return intersects(bbox, EXTENTS)


def _export(service: str, bbox, h: int, wd: int) -> np.ndarray:
    n, s, e, w = as_nsew(bbox)
    params = {
        "bbox": f"{w},{s},{e},{n}", "bboxSR": 4326, "imageSR": 4326, "size": f"{wd},{h}",
        "format": "tiff", "pixelType": "F32", "noData": -9999,
        "interpolation": "RSP_BilinearInterpolation", "f": "image",
    }
    ctx = f"ČÚZK {service}"
    arr, _t, _crs, nodata = read_geotiff_array(
        http_get(SERVICE_URL.format(service=service), params, ctx), ctx)
    if arr.shape != (h, wd):
        raise SurveyError(f"{ctx}: asked for {wd}x{h}, got {arr.shape[1]}x{arr.shape[0]}")
    bad = (arr <= -9999) | (arr == 0)
    if nodata is not None:
        bad |= arr == nodata
    arr[bad] = np.nan
    return arr


def _fetch(bbox, res: float):
    h, wd, transform = lonlat_grid(bbox, res)
    surface = _export(SURFACE, bbox, h, wd)
    ground = _export(GROUND, bbox, h, wd)
    return clean_heights(surface - ground), transform


def ndsm_for_bbox(bbox, resolution_m: float = resolution_m):
    """DMP 1G − DMR 5G over ``bbox`` on a lon/lat grid, or None outside Czechia."""
    if not covers(bbox):
        return None
    return cached_ndsm(f"survey_{name}", bbox, resolution_m, lambda: _fetch(bbox, resolution_m))
