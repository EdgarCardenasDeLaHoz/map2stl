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

import logging
from typing import Tuple

import numpy as np
import requests

from city2stl.skyline.height import BBox, HeightResult, _resample
from ._cache import (
    register_ttl, make_cache_key, read_height_result, write_height_result,
)
from ._raster import read_geotiff_bytes

logger = logging.getLogger(__name__)

register_ttl("ghsl", 180)  # 180 days

_CONFIDENCE = 0.4  # low — 100m is very coarse for building heights
_RESOLUTION_M = 100.0
_NAMESPACE = "ghsl"
_DOWNLOAD_TIMEOUT = 120

# GHSL WMS endpoints — tried in order, first successful response wins.
# The data moved from JRC GeoServer to the Copernicus Human Settlement portal.
_WMS_URLS = [
    "https://human-settlement.emergency.copernicus.eu/geoserver/ows",
    "https://ghsl.jrc.ec.europa.eu/geoserver/ows",
]
_LAYER_NAME = "GHS_BUILT_H_ANBH_E2018_GLOBE_R2023A_54009_100_V1_0"


def _fetch_ghsl_wms(bbox: BBox, dim: Tuple[int, int]) -> np.ndarray | None:
    """Fetch building heights from GHSL WMS endpoint.

    Tries each URL in _WMS_URLS in order; returns the first successful raster.
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

    logger.info("GHSL: fetching building height for "
                "N=%.3f S=%.3f E=%.3f W=%.3f", north, south, east, west)

    for wms_url in _WMS_URLS:
        try:
            r = requests.get(wms_url, params=params, timeout=_DOWNLOAD_TIMEOUT)
            if r.status_code >= 400:
                logger.debug("GHSL WMS %s returned %d", wms_url, r.status_code)
                continue
            r.raise_for_status()
            ct = r.headers.get("Content-Type", "")
            if "xml" in ct.lower() or "html" in ct.lower():
                logger.debug("GHSL WMS %s returned non-image: %s", wms_url, ct)
                continue
            arr = _parse_tiff_bytes(r.content)
            if arr is not None:
                logger.info("GHSL: success via %s", wms_url)
                return arr
        except requests.RequestException as e:
            logger.debug("GHSL WMS %s failed: %s", wms_url, e)

    logger.warning("GHSL: all WMS endpoints failed for bbox")
    return None


def _parse_tiff_bytes(data: bytes) -> np.ndarray | None:
    """Parse building-height GeoTIFF/TIFF bytes (zero/negative = no building)."""
    return read_geotiff_bytes(data, zero_as_nodata=True, context="GHSL TIFF")


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

        hit = read_height_result(_NAMESPACE, cache_key, self.name, _RESOLUTION_M)
        if hit is not None:
            return hit

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

        write_height_result(_NAMESPACE, cache_key, result)
        return result


def _empty_result(dim: Tuple[int, int]) -> HeightResult:
    h, w = dim
    return HeightResult(
        raster=np.full((h, w), np.nan, dtype=np.float32),
        confidence=np.zeros((h, w), dtype=np.float32),
        source_name="ghsl",
        resolution_m=_RESOLUTION_M,
    )
