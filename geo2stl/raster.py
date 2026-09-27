"""Raster utilities shared by terrain, height-provider and satellite layer flows.

- ``read_geotiff`` — GeoTIFF bytes / path → (array, transform, crs, nodata).
- Fetch-scale helpers for satellite layers.
"""

from __future__ import annotations

import io
import math
import os
from pathlib import Path
from typing import Any

import numpy as np

from geo2stl.geo import bbox_size_m


def _fit_shape(src_h: int, src_w: int, fit_dim: int) -> tuple[int, int]:
    """Output shape whose longer side is *fit_dim*, keeping the aspect ratio."""
    if src_h >= src_w:
        return fit_dim, max(1, int(fit_dim * src_w / src_h))
    return max(1, int(fit_dim * src_h / src_w)), fit_dim


def read_geotiff(
    source: bytes | io.BytesIO | str | os.PathLike,
    *,
    band: int = 1,
    fit_dim: int | None = None,
    zero_as_nodata: bool = False,
    dtype: Any = np.float32,
) -> tuple[np.ndarray, Any, str | None, float | None]:
    """Read one band of a GeoTIFF from bytes, a buffer or a path.

    Uses rasterio; when rasterio is not installed or cannot decode the data,
    falls back to PIL (pixels only: ``transform``, ``crs`` and ``nodata`` are
    then None).

    Args:
        source: Raw bytes, a ``BytesIO`` buffer, or a file path.
        band: 1-based band index (rasterio only).
        fit_dim: If set, resample (bilinear) so the longer side is *fit_dim*
            pixels, keeping the aspect ratio. The returned transform is the
            file's own, not rescaled.
        zero_as_nodata: Treat samples ``<= 0`` as no data (building-height
            products). DEMs leave this False: 0 m and below sea level are real.
        dtype: Output dtype; must be floating so no-data can be NaN.

    Returns:
        ``(array, transform, crs, nodata)``: *array* has no-data samples set
        to NaN; *transform* is a rasterio ``Affine``; *crs* is a string.

    Raises:
        ValueError: neither backend could decode *source*.
    """
    if isinstance(source, (bytes, bytearray)):
        buf: Any = io.BytesIO(source)
    elif isinstance(source, io.BytesIO):
        buf = source
    else:
        buf = str(Path(source))

    rio_error: Exception | None = None
    try:
        import rasterio  # noqa: PLC0415
        from rasterio.enums import Resampling  # noqa: PLC0415
    except ImportError:
        rasterio = None
    if rasterio is not None:
        try:
            with rasterio.open(buf) as src:
                if fit_dim:
                    raw = src.read(band, out_shape=_fit_shape(src.height, src.width, fit_dim),
                                   resampling=Resampling.bilinear)
                else:
                    raw = src.read(band)
                nodata = src.nodata
                arr = raw.astype(dtype)
                if nodata is not None:
                    arr[raw == nodata] = np.nan
                if zero_as_nodata:
                    arr[~(arr > 0)] = np.nan
                return arr, src.transform, (str(src.crs) if src.crs else None), nodata
        except Exception as exc:  # noqa: BLE001 - fall through to PIL
            rio_error = exc

    try:
        from PIL import Image  # noqa: PLC0415
        if hasattr(buf, "seek"):
            buf.seek(0)
        img = Image.open(buf)
        if fit_dim:
            img = img.resize(_fit_shape(img.height, img.width, fit_dim)[::-1],
                             Image.Resampling.BILINEAR)
        arr = np.array(img).astype(dtype)
        if zero_as_nodata:
            arr[~(arr > 0)] = np.nan
        return arr, None, None, None
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"cannot decode GeoTIFF: {rio_error or exc}") from exc


def bbox_longer_side_m(north: float, south: float, east: float, west: float) -> float:
    """Return the longer bbox side in metres, clamped to at least 1m."""
    return max(*bbox_size_m({"north": north, "south": south, "east": east, "west": west}), 1.0)


def derive_sat_scale(north: float, south: float, east: float, west: float, dim: int) -> int:
    """Derive a metres-per-pixel fetch scale from bbox and target dimension."""
    return max(10, int(math.ceil(bbox_longer_side_m(north, south, east, west) / dim)))


def clamp_esa_scale(north: float, south: float, east: float, west: float, sat_scale: int) -> int:
    """Clamp ESA fetch scale to stay below EE response and pixel-dimension limits."""
    bbox_w_m, bbox_h_m = bbox_size_m({"north": north, "south": south, "east": east, "west": west})

    max_esa_px = 50_331_648 // 2
    est_px = (bbox_w_m / sat_scale) * (bbox_h_m / sat_scale)
    if est_px > max_esa_px:
        sat_scale = max(
            sat_scale,
            int(math.ceil(math.sqrt(bbox_w_m * bbox_h_m / max_esa_px))),
        )

    min_safe_dim = max(
        int(math.ceil(bbox_w_m / 32768)),
        int(math.ceil(bbox_h_m / 32768)),
        1,
    )
    return max(sat_scale, min_safe_dim)
