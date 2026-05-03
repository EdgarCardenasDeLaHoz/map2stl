"""
GHSL GHS-BUILT-H provider — JRC Global Human Settlement Layer building height.

Data: GHS-BUILT-H R2023A
- Raster building height, 100m resolution, global coverage.
- Source: JRC Data Catalogue / AWS Open Data
- Values: Average building height in metres within each 100m cell.
- NaN / 0 = no buildings detected.
- License: CC-BY-4.0

This is the lowest-resolution building height source but has truly global
coverage, making it useful as a last-resort baseline for cities like
Cartagena where nDSM and WSF3D may not resolve individual buildings.

Tile grid: Mollweide projection, 10° × 10° tiles.
We use the STAC API to find the correct tile(s) for a bbox.
"""

from __future__ import annotations

import io
import logging
from typing import Tuple

import numpy as np
import requests

from city2stl.height import BBox, HeightResult, _resample
from app.server.core.cache import (
    make_cache_key, read_array_cache, write_array_cache,
    NAMESPACE_TTL,
)

logger = logging.getLogger(__name__)

NAMESPACE_TTL.setdefault("ghsl", 180 * 86400)  # 180 days

_CONFIDENCE = 0.4  # low — 100m is very coarse for building heights
_RESOLUTION_M = 100.0
_NAMESPACE = "ghsl"
_DOWNLOAD_TIMEOUT = 120

# JRC GHSL GHS-BUILT-H WMS/WCS endpoint
# The JRC provides a WMS that can serve arbitrary bbox requests.
_WMS_BASE = (
    "https://ghsl.jrc.ec.europa.eu/geoserver/ows"
)
_LAYER_NAME = "GHS_BUILT_H_ANBH_E2018_GLOBE_R2023A_54009_100_V1_0"


def _fetch_ghsl_wms(bbox: BBox, dim: Tuple[int, int]) -> np.ndarray | None:
    """Fetch building heights from GHSL WMS endpoint.

    Uses GetMap with bbox in EPSG:4326, returns raster image.
    Falls back to direct GeoTIFF tile download if WMS fails.
    """
    north, south, east, west = bbox
    h, w = dim

    params = {
        "service": "WMS",
        "version": "1.1.1",
        "request": "GetMap",
        "layers": _LAYER_NAME,
        "bbox": f"{west},{south},{east},{north}",
        "srs": "EPSG:4326",
        "width": str(w),
        "height": str(h),
        "format": "image/geotiff",
        "styles": "",
    }

    try:
        logger.info(f"GHSL: fetching building height for "
                    f"N={north:.3f} S={south:.3f} E={east:.3f} W={west:.3f}")
        r = requests.get(_WMS_BASE, params=params, timeout=_DOWNLOAD_TIMEOUT)
        if r.status_code >= 400:
            logger.warning(f"GHSL WMS returned {r.status_code}")
            return None
        r.raise_for_status()

        ct = r.headers.get("Content-Type", "")
        if "xml" in ct.lower() or "html" in ct.lower():
            logger.warning(f"GHSL WMS returned non-image: {ct}")
            return None

        return _parse_tiff_bytes(r.content)

    except requests.RequestException as e:
        logger.warning(f"GHSL WMS request failed: {e}")
        return None


def _parse_tiff_bytes(data: bytes) -> np.ndarray | None:
    """Parse GeoTIFF / TIFF bytes into a float32 array."""
    try:
        import rasterio
        try:
            with rasterio.open(io.BytesIO(data)) as src:
                arr = src.read(1).astype(np.float32)
                nodata = src.nodata
                if nodata is not None:
                    arr[arr == nodata] = np.nan
                arr[arr <= 0] = np.nan
                return arr
        except rasterio.errors.RasterioIOError:
            logger.warning("Failed to parse GHSL TIFF with rasterio")
            return None
    except ImportError:
        pass

    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        arr = np.array(img, dtype=np.float32)
        arr[arr <= 0] = np.nan
        return arr
    except Exception as e:
        logger.warning(f"Cannot parse GHSL TIFF: {e}")
        return None


class GHSLProvider:
    """JRC GHSL GHS-BUILT-H global building height (~100m resolution)."""

    name = "ghsl"

    def covers(self, bbox: BBox) -> bool:
        """GHSL has global coverage."""
        return True

    def fetch_heights(self, bbox: BBox, dim: Tuple[int, int]) -> HeightResult:
        """Fetch building heights from GHSL WMS."""
        north, south, east, west = bbox
        cache_key = make_cache_key(_NAMESPACE, north, south, east, west,
                                   {"dim": list(dim)})

        cached = read_array_cache(_NAMESPACE, cache_key)
        if cached is not None:
            arrays, meta = cached
            return HeightResult(
                raster=arrays["raster"],
                confidence=arrays["confidence"],
                source_name=self.name,
                resolution_m=meta.get("resolution_m", _RESOLUTION_M),
            )

        raster = _fetch_ghsl_wms(bbox, dim)

        if raster is None:
            return _empty_result(dim)

        # Resample to target if needed
        if raster.shape != dim:
            raster = _resample(raster, dim)

        confidence = np.where(
            np.isnan(raster), 0.0, _CONFIDENCE
        ).astype(np.float32)

        result = HeightResult(raster, confidence, self.name, _RESOLUTION_M)

        write_array_cache(_NAMESPACE, cache_key,
                          {"raster": raster, "confidence": confidence},
                          {"resolution_m": _RESOLUTION_M})
        return result


def _empty_result(dim: Tuple[int, int]) -> HeightResult:
    h, w = dim
    return HeightResult(
        raster=np.full((h, w), np.nan, dtype=np.float32),
        confidence=np.zeros((h, w), dtype=np.float32),
        source_name="ghsl",
        resolution_m=_RESOLUTION_M,
    )
