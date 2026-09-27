"""Andalucía: REDIAM MDHN, 1 m height above ground from PNOA LiDAR 2020-21.

The Junta de Andalucía's environmental data network (REDIAM) publishes the
normalised height model (MDHN, "modelo digital de altura normalizada") as one
regional **cloud-optimised GeoTIFF** (EPSG:25830, 1 m, float32, ~284 GB) in its
open repository. A COG is read by HTTP range requests, so a landmark's window
costs a few hundred kilobytes: GDAL's ``/vsicurl/`` driver fetches only the
tiles under it. Checked 2026-09-27 over Granada Cathedral (0-52 m, ~6 s cold).

Heights include vegetation (it is surface minus ground); clipped to a building
footprint that is the roof. The file's nodata tag says 255 but empty cells hold
-9999; both are treated as no data.

Coverage: Andalucía (extent check below).
"""

from __future__ import annotations

import logging

from ._survey import SurveyError, as_nsew, cached_ndsm, clean_heights, intersects, warp_to_grid

logger = logging.getLogger(__name__)

name = "rediam_mdhn"
label = "REDIAM MDHN (Andalucía, 1 m, 2020-21)"
resolution_m = 1.0

COG_URL = ("https://portalrediam.cica.es/repositorio/01_CARACTERIZACION_TERRITORIO/"
           "07_BASES_REF_ELEV/07_ALTURA_NORMALIZADA/01_PROYECTOS_REGIONALES/"
           "2020-21_AND_PNOA_LiDAR_V226_hn/InfGeografica/InfRaster/ETRS89_h30_Horto/COG/"
           "Mos_1m/Mos_MDHN_1m_COG.tif")
#: Andalucía (n, s, e, w).
EXTENTS = [(38.73, 35.99, -1.62, -7.56)]
#: Source cells read beyond the bbox on each side, so bilinear resampling at the
#: edge has neighbours.
_PAD_PX = 2


def covers(bbox) -> bool:
    return intersects(bbox, EXTENTS)


def _read_window(bbox):
    """``(array, transform, crs)`` of the COG under ``bbox`` (native CRS, padded)."""
    import rasterio
    from rasterio.errors import RasterioIOError
    from rasterio.warp import transform_bounds
    from rasterio.windows import Window, from_bounds

    n, s, e, w = as_nsew(bbox)
    env = {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR", "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
           "GDAL_HTTP_TIMEOUT": "60", "GDAL_HTTP_MAX_RETRY": "2"}
    try:
        with rasterio.Env(**env), rasterio.open("/vsicurl/" + COG_URL) as ds:
            win = from_bounds(*transform_bounds("EPSG:4326", ds.crs, w, s, e, n), ds.transform)
            win = Window(win.col_off - _PAD_PX, win.row_off - _PAD_PX,
                         win.width + 2 * _PAD_PX, win.height + 2 * _PAD_PX).round_offsets().round_lengths()
            arr = ds.read(1, window=win, boundless=True, fill_value=-9999).astype("float32")
            return arr, ds.window_transform(win), ds.crs
    except RasterioIOError as exc:
        raise SurveyError(f"REDIAM MDHN: cannot read the COG ({exc})") from exc


def _fetch(bbox, res: float):
    arr, transform, crs = _read_window(bbox)
    arr[arr == 255] = float("nan")          # the file's nodata tag
    arr = clean_heights(arr)
    return warp_to_grid(arr, transform, crs, bbox, res)


def ndsm_for_bbox(bbox, resolution_m: float = resolution_m):
    """MDHN over ``bbox`` on a lon/lat grid, or None outside Andalucía."""
    if not covers(bbox):
        return None
    return cached_ndsm(f"survey_{name}", bbox, resolution_m, lambda: _fetch(bbox, resolution_m))
