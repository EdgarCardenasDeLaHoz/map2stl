"""
Copernicus EU Building Height provider (10 m, Europe only).

Data: the Copernicus Land Monitoring Service Building Height 2012 raster, read
through the EEA discomap WCS (``_EU_WCS_URL``). HTTPS, no API key. CC-BY-4.0.
Outside Europe ``covers`` is False; WSF3D and nDSM cover the global case. (A
GHSL GHS-BUILT-H global fallback was never implemented; its stub was removed
2026-10-05.)
"""

from __future__ import annotations

import logging

import numpy as np
import requests

from city2stl.height import BBox, HeightResult, _resample

from ._cache import (
    make_cache_key,
    read_height_result,
    register_ttl,
    write_height_result,
)
from ._raster import read_geotiff_bytes

logger = logging.getLogger(__name__)

register_ttl("copernicus_bh", 90)

_CONFIDENCE = 0.7
_RESOLUTION_M = 10.0  # EU product
_NAMESPACE = "copernicus_bh"
_DOWNLOAD_TIMEOUT = 90

# EU Building Height WCS:
_EU_WCS_URL = (
    "https://image.discomap.eea.europa.eu/arcgis/services/"
    "GioLand/BuildingHeight2012/MapServer/WCSServer"
)


def _is_in_europe(bbox: BBox) -> bool:
    """Check if bbox overlaps the European coverage area."""
    north, south, east, west = bbox
    # Rough European bounds (including Turkey, Iceland, parts of Russia)
    return (south < 72 and north > 34 and west < 45 and east > -25)


def _fetch_eu_wcs(bbox: BBox, dim: tuple[int, int]) -> np.ndarray | None:
    """Fetch building heights from Copernicus EU WCS endpoint.

    Returns float32 array (H, W) in metres, or None on failure.
    """
    north, south, east, west = bbox
    h, w = dim

    params = {
        "service": "WCS",
        "version": "1.1.1",
        "request": "GetCoverage",
        "identifier": "1",  # Building Height layer
        "format": "image/tiff",
        "GridBaseCRS": "urn:ogc:def:crs:EPSG::4326",
        "BoundingBox": f"{south},{west},{north},{east},urn:ogc:def:crs:EPSG::4326",
        "GridOffsets": f"{(north - south) / h},{(east - west) / w}",
        "GridType": "urn:ogc:def:method:WCS:1.1:2dSimpleGrid",
        "width": str(w),
        "height": str(h),
    }

    try:
        logger.info(f"Copernicus EU WCS: fetching building height for "
                    f"N={north:.3f} S={south:.3f} E={east:.3f} W={west:.3f}")
        r = requests.get(_EU_WCS_URL, params=params, timeout=_DOWNLOAD_TIMEOUT)
        if r.status_code == 404 or r.status_code >= 500:
            logger.warning(f"Copernicus EU WCS returned {r.status_code}")
            return None
        r.raise_for_status()

        # Check if response is a GeoTIFF (not an XML error)
        ct = r.headers.get("Content-Type", "")
        if "xml" in ct.lower() or "html" in ct.lower():
            logger.warning(f"Copernicus EU WCS returned non-image: {ct}")
            return None

        return _parse_geotiff_bytes(r.content)

    except requests.RequestException as e:
        logger.warning(f"Copernicus EU WCS request failed: {e}")
        return None


def _parse_geotiff_bytes(data: bytes) -> np.ndarray | None:
    """Parse building-height GeoTIFF bytes (zero/negative = no building)."""
    return read_geotiff_bytes(data, zero_as_nodata=True, context="Copernicus GeoTIFF")


class CopernicusProvider:
    """Copernicus EU Building Height (10m, Europe)."""

    name = "copernicus"

    def covers(self, bbox: BBox) -> bool:
        """Returns True for European bboxes (primary coverage area)."""
        return _is_in_europe(bbox)

    def fetch_heights(self, bbox: BBox, dim: tuple[int, int]) -> HeightResult:
        """Fetch building heights from the EU WCS (empty outside Europe or on failure)."""
        north, south, east, west = bbox
        cache_key = make_cache_key(_NAMESPACE, north, south, east, west,
                                   {"dim": list(dim)})

        # Check cache
        hit = read_height_result(_NAMESPACE, cache_key, self.name, _RESOLUTION_M)
        if hit is not None:
            return hit

        raster = _fetch_eu_wcs(bbox, dim) if _is_in_europe(bbox) else None
        if raster is None:
            return _empty_result(dim)

        # Resample to target dim if needed
        if raster.shape != dim:
            raster = _resample(raster, dim)

        confidence = np.where(
            np.isnan(raster), 0.0, _CONFIDENCE
        ).astype(np.float32)

        result = HeightResult(raster, confidence, self.name, _RESOLUTION_M)

        # Cache
        write_height_result(_NAMESPACE, cache_key, result)
        return result


def _empty_result(dim: tuple[int, int]) -> HeightResult:
    return HeightResult.empty(dim, "copernicus", _RESOLUTION_M)
