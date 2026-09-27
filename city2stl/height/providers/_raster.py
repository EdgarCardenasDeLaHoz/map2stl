"""GeoTIFF byte parsing for height providers.

A thin wrapper over :func:`geo2stl.raster.read_geotiff` that returns just the
array, or None when the bytes cannot be decoded (providers treat that as "no
data"). ``zero_as_nodata`` is True for building-height products (GHSL,
Copernicus building height) and False for bare elevation DEMs (nDSM, 3DEP),
where 0 m and below-sea-level values are legitimate.
"""

from __future__ import annotations

import io
import logging

import numpy as np

from geo2stl.raster import read_geotiff

logger = logging.getLogger(__name__)

__all__ = ["read_geotiff_bytes"]


def read_geotiff_bytes(
    data: bytes | io.BytesIO,
    *,
    zero_as_nodata: bool = False,
    context: str = "GeoTIFF",
) -> np.ndarray | None:
    """Parse GeoTIFF *data* into a float32 array, or None if it cannot be decoded.

    Args:
        data:           Raw bytes or a ``BytesIO`` buffer.
        zero_as_nodata: When True, samples ``<= 0`` are set to NaN (building
                        height semantics). DEM callers leave this False.
        context:        Label used in warning messages.
    """
    try:
        return read_geotiff(data, zero_as_nodata=zero_as_nodata)[0]
    except ValueError as e:
        logger.warning("Cannot parse %s bytes: %s", context, e)
        return None
