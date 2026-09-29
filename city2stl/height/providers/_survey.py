"""Shared plumbing for the surveyed nDSM providers (F-LANDMARK §4).

Every survey provider answers one question: *height above ground (metres) over a
small bbox, at about ``resolution_m``*, for a landmark's footprint (the nDSM
override in :mod:`city2stl.landmarks`). The contract is::

    ndsm_for_bbox(bbox, resolution_m) -> (array, transform) | None

* ``bbox`` is ``(north, south, east, west)`` in degrees (a ``{north, ...}`` dict
  is accepted too);
* ``array`` is float32 ``(H, W)``, **row 0 = north**, metres above ground, NaN
  where the survey has no value;
* ``transform`` is a ``rasterio.Affine`` from (col, row) to (lon, lat), EPSG:4326
  -- the grid covers ``bbox`` exactly (:func:`lonlat_grid`);
* ``None`` means the survey does not cover the bbox (outside its extent, or every
  cell came back empty). Transport and decoding failures raise
  :class:`SurveyError` with the endpoint named, so the UI can tell "no data here"
  from "the service is down".

Results are cached per (provider, bbox, resolution) in the shared array cache
(``geo2stl.cache``) -- see :func:`cached_ndsm`. Nothing here talks to the
network; the providers do.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable

import numpy as np
from rasterio.transform import Affine

from geo2stl.cache import make_cache_key, read_array_cache, write_array_cache

from ._cache import register_ttl

logger = logging.getLogger(__name__)

#: Largest grid side a survey request may produce. A landmark footprint at 0.5 m
#: is a few hundred cells; this guards against a whole-city request by mistake.
MAX_GRID_SIDE = 4096
#: Metres per degree of latitude (mean).
M_PER_DEG_LAT = 111_320.0

NdsmResult = tuple[np.ndarray, Affine]


class SurveyError(RuntimeError):
    """A survey endpoint failed (HTTP error, undecodable payload, oversize request)."""


def as_nsew(bbox) -> tuple[float, float, float, float]:
    """``(north, south, east, west)`` from a tuple or a ``{north, south, east, west}`` dict."""
    if isinstance(bbox, dict):
        return (float(bbox["north"]), float(bbox["south"]),
                float(bbox["east"]), float(bbox["west"]))
    n, s, e, w = (float(v) for v in bbox)
    return n, s, e, w


def intersects(bbox, extents: list[tuple[float, float, float, float]]) -> bool:
    """True when ``bbox`` overlaps any of ``extents`` (each ``(n, s, e, w)``)."""
    n, s, e, w = as_nsew(bbox)
    return any(s < en and n > es and w < ee and e > ew for en, es, ee, ew in extents)


def lonlat_grid(bbox, resolution_m: float) -> tuple[int, int, Affine]:
    """``(H, W, transform)`` of a lon/lat grid covering ``bbox`` at about ``resolution_m``."""
    n, s, e, w = as_nsew(bbox)
    if not (n > s and e > w):
        raise ValueError(f"degenerate bbox {bbox!r}")
    if resolution_m <= 0:
        raise ValueError("resolution_m must be positive")
    lat = math.radians((n + s) / 2)
    h = max(1, math.ceil((n - s) * M_PER_DEG_LAT / resolution_m))
    wd = max(1, math.ceil((e - w) * M_PER_DEG_LAT * math.cos(lat) / resolution_m))
    if h > MAX_GRID_SIDE or wd > MAX_GRID_SIDE:
        raise SurveyError(f"{wd}x{h} cells at {resolution_m} m is over the {MAX_GRID_SIDE}-cell "
                          f"limit per side; request a smaller bbox or a coarser resolution")
    return h, wd, Affine((e - w) / wd, 0.0, w, 0.0, -(n - s) / h, n)


def warp_to_grid(arr: np.ndarray, src_transform, src_crs, bbox, resolution_m: float,
                 nodata: float | None = None) -> NdsmResult:
    """Resample a source raster (any CRS) onto :func:`lonlat_grid` (bilinear, NaN = no data)."""
    from rasterio.warp import Resampling, reproject

    h, wd, dst_t = lonlat_grid(bbox, resolution_m)
    src = np.asarray(arr, dtype=np.float32)
    if nodata is not None and np.isfinite(nodata):
        src = np.where(src == nodata, np.nan, src)
    dst = np.full((h, wd), np.nan, dtype=np.float32)
    reproject(source=src, destination=dst, src_transform=src_transform, src_crs=src_crs,
              src_nodata=np.nan, dst_transform=dst_t, dst_crs="EPSG:4326", dst_nodata=np.nan,
              resampling=Resampling.bilinear)
    return dst, dst_t


def clean_heights(arr: np.ndarray, lo: float = -5.0, hi: float = 400.0) -> np.ndarray:
    """Height above ground with sentinels (``-9999``, ``255``, ...) as NaN and small
    negatives (DSM/DTM noise) clamped to 0."""
    a = np.asarray(arr, dtype=np.float32).copy()
    a[~np.isfinite(a) | (a < lo) | (a > hi)] = np.nan
    return np.where(a < 0, 0.0, a).astype(np.float32)


def http_get(url: str, params, context: str, timeout: float = 90.0) -> bytes:
    """GET ``url`` and return the body; any transport error or non-200 is a SurveyError."""
    import requests

    try:
        resp = requests.get(url, params=params, timeout=timeout)
    except requests.RequestException as exc:
        raise SurveyError(f"{context}: {url} unreachable ({exc})") from exc
    if resp.status_code != 200:
        raise SurveyError(f"{context}: {url} answered HTTP {resp.status_code}: "
                          f"{resp.text[:200].strip()!r}")
    return resp.content


def read_geotiff_array(data: bytes, context: str):
    """``(array float32, transform, crs, nodata)`` of GeoTIFF bytes; SurveyError if it is not one.

    A map service can answer with a rendered picture of the elevation rather than
    the elevation (``docs/reference/survey-sources.md``, "two traps"): anything that is not a
    single float/int band is refused rather than read as heights.
    """
    import io

    import rasterio
    from rasterio.errors import RasterioIOError

    if data[:4] not in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):
        head = data[:200].decode("utf-8", "replace").strip()
        raise SurveyError(f"{context}: expected a GeoTIFF, got {head!r}")
    try:
        with rasterio.open(io.BytesIO(data)) as ds:
            if ds.count != 1 or ds.dtypes[0] in ("uint8",):
                raise SurveyError(f"{context}: {ds.count} band(s) of {ds.dtypes[0]} -- a picture "
                                  f"of the elevation, not values")
            return ds.read(1).astype(np.float32), ds.transform, ds.crs, ds.nodata
    except RasterioIOError as exc:
        raise SurveyError(f"{context}: cannot decode GeoTIFF ({exc})") from exc


def cached_ndsm(namespace: str, bbox, resolution_m: float,
                fetch: Callable[[], NdsmResult | None], ttl_days: int = 180) -> NdsmResult | None:
    """Return the cached nDSM for (namespace, bbox, resolution), else ``fetch()`` and cache it.

    A ``None`` (no coverage) and an all-NaN grid are not cached, so a survey that
    is extended later is picked up.
    """
    register_ttl(namespace, ttl_days)
    n, s, e, w = as_nsew(bbox)
    key = make_cache_key(namespace, n, s, e, w, {"resolution_m": round(float(resolution_m), 3)})
    hit = read_array_cache(namespace, key)
    if hit is not None:
        arrays, meta = hit
        t = meta.get("transform")
        if t and "ndsm" in arrays:
            return arrays["ndsm"].astype(np.float32), Affine(*t[:6])
    got = fetch()
    if got is None:
        return None
    arr, transform = got
    if not np.isfinite(arr).any():
        return None
    write_array_cache(namespace, key, {"ndsm": arr},
                      {"transform": list(transform)[:6], "resolution_m": resolution_m})
    return arr, transform
