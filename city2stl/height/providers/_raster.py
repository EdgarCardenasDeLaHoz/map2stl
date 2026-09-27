"""Shared GeoTIFF byte parsing for height providers.

Several providers downloaded a GeoTIFF over HTTP and parsed it into a float32
array with the same rasterio→PIL fallback. The only real difference was whether
zero/negative samples mean "no data" — true for building-height products
(GHSL, Copernicus building height) and false for bare elevation DEMs (nDSM,
3DEP), where 0 m and below-sea-level values are legitimate. That choice is now a
single ``zero_as_nodata`` flag.
"""

from __future__ import annotations

import io
import logging

import numpy as np

logger = logging.getLogger(__name__)

__all__ = ["read_geotiff_bytes"]


def read_geotiff_bytes(
    data: bytes | io.BytesIO,
    *,
    zero_as_nodata: bool = False,
    context: str = "GeoTIFF",
) -> np.ndarray | None:
    """Parse GeoTIFF *data* into a float32 array (``rasterio`` → ``PIL`` fallback).

    Args:
        data:           Raw bytes or a ``BytesIO`` buffer.
        zero_as_nodata: When True, samples ``<= 0`` are set to NaN (building
                        height semantics). DEM callers leave this False.
        context:        Label used in warning messages.

    Returns the array, or ``None`` if neither backend could decode the bytes.
    """
    buf = data if isinstance(data, io.BytesIO) else io.BytesIO(data)

    try:
        import rasterio  # noqa: PLC0415
        with rasterio.open(buf) as src:
            arr = src.read(1).astype(np.float32)
            if src.nodata is not None:
                arr[arr == src.nodata] = np.nan
            if zero_as_nodata:
                arr[arr <= 0] = np.nan
            return arr
    except ImportError:
        pass
    except Exception:
        logger.warning("Failed to parse %s with rasterio", context)
        # fall through to the PIL fallback

    try:
        from PIL import Image  # noqa: PLC0415
        buf.seek(0)
        arr = np.array(Image.open(buf), dtype=np.float32)
        if zero_as_nodata:
            arr[arr <= 0] = np.nan
        return arr
    except Exception as e:  # noqa: BLE001
        logger.warning("Cannot parse %s bytes: %s", context, e)
        return None
