"""Spain: CNIG / IGN "MDSn de edificación", building heights from PNOA LiDAR.

The national normalised surface model restricted to the **building** class
(``mdsn_e025``: 2.5 m, first PNOA-LiDAR coverage, 2008-2015) is published through
IDEE's documented INSPIRE WCS 2.0.1 at ``wcs-mds.idee.es/mds`` (coverages
``mds05`` surface, ``mdsn_e025`` buildings, ``mdsn_v025`` vegetation). No key.
Checked 2026-09-27 over Cartagena city hall (0-35 m).

The coverage is stored in EPSG:3042 (ETRS89 / UTM 30N, northing first, extended
over the whole country); the subset is given in that projection's easting /
northing and the answer (int16 metres, so heights are whole metres) is warped
onto the lon/lat grid here.

Not implemented: the 0.5 m surface rasters and point clouds of the second and
third PNOA coverages. They are only delivered through ``centrodedescargas.cnig.es``
by an undocumented pair of form requests (``archivosTotalesSerieVisor`` then a
POST to ``descargaDir``; a plain GET is refused) -- see ``docs/reference/survey-sources.md``.
That is scraping a download form, so it is left out; in Andalucía use
``rediam_mdhn`` (1 m, 2020-21) instead.

Coverage: Spain incl. the Balearic and Canary Islands.
"""

from __future__ import annotations

import logging

from ._survey import (
    as_nsew,
    cached_ndsm,
    clean_heights,
    http_get,
    intersects,
    read_geotiff_array,
    warp_to_grid,
)

logger = logging.getLogger(__name__)

name = "cnig_mdsn"
label = "CNIG MDSn edificación (Spain, 2.5 m, 2008-15)"
resolution_m = 2.5

WCS_URL = "https://wcs-mds.idee.es/mds"
COVERAGE = "mdsn_e025"
#: Mainland Spain + Balearics, Canary Islands (n, s, e, w).
EXTENTS = [(43.9, 35.9, 4.4, -9.4), (29.5, 27.6, -13.3, -18.2)]
_PAD_M = 5.0


def covers(bbox) -> bool:
    return intersects(bbox, EXTENTS)


def _fetch(bbox, res: float):
    from pyproj import Transformer

    n, s, e, w = as_nsew(bbox)
    to_utm = Transformer.from_crs("EPSG:4326", "EPSG:25830", always_xy=True)
    xs, ys = to_utm.transform([w, e, w, e], [s, s, n, n])
    x0, x1 = min(xs) - _PAD_M, max(xs) + _PAD_M
    y0, y1 = min(ys) - _PAD_M, max(ys) + _PAD_M
    params = [("SERVICE", "WCS"), ("VERSION", "2.0.1"), ("REQUEST", "GetCoverage"),
              ("COVERAGEID", COVERAGE), ("FORMAT", "image/tiff"),
              ("SUBSET", f"x({x0:.1f},{x1:.1f})"), ("SUBSET", f"y({y0:.1f},{y1:.1f})")]
    ctx = f"IDEE WCS {COVERAGE}"
    arr, transform, crs, nodata = read_geotiff_array(http_get(WCS_URL, params, ctx), ctx)
    if nodata is not None:
        arr[arr == nodata] = float("nan")
    return warp_to_grid(clean_heights(arr), transform, crs, bbox, res)


def ndsm_for_bbox(bbox, resolution_m: float = resolution_m):
    """Building nDSM over ``bbox`` on a lon/lat grid, or None outside Spain."""
    if not covers(bbox):
        return None
    return cached_ndsm(f"survey_{name}", bbox, resolution_m, lambda: _fetch(bbox, resolution_m))
