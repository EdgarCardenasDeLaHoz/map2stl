"""
Copernicus EU Building Height provider.

Data: GHS-BUILT-H from the GHSL (Global Human Settlement Layer) project.
- Raster building height, 10m resolution within Europe, 100m globally.
- European coverage via Copernicus Land Monitoring Service.
- Access: HTTPS download, no API key required.

We use the JRC GHSL R2023A release which provides building heights globally.
For European cities, the 10m EU product is available via WCS.
Fallback: JRC GHSL GHS-BUILT-H 100m (global).

License: CC-BY-4.0

Alternative approach: Copernicus Land Monitoring Service provides Urban Atlas
Building Height (10m, EU only) but access requires user registration and
download by Functional Urban Area. The GHSL WMS/WCS endpoint is simpler.
"""

from __future__ import annotations

import io
import logging
from typing import Tuple

import numpy as np
import requests

from city2stl.skyline.height import BBox, HeightResult, _resample
from app.server.core.cache import (
    make_cache_key, read_array_cache, write_array_cache,
    NAMESPACE_TTL,
)

logger = logging.getLogger(__name__)

NAMESPACE_TTL.setdefault("copernicus_bh", 90 * 86400)

_CONFIDENCE = 0.7
_RESOLUTION_M = 10.0  # EU product
_NAMESPACE = "copernicus_bh"
_DOWNLOAD_TIMEOUT = 90

# GHSL GHS-BUILT-H R2023A — JRC HTTPS tile distribution
# The data is distributed as 10° × 10° tiles in Mollweide projection.
# For our use case, we use the OGC WCS endpoint which accepts
# geographic bounding boxes and returns GeoTIFF directly.

# JRC Data Portal WCS endpoint (no key required):
_WCS_BASE = (
    "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/"
    "GHS_BUILT_H_GLOBE_R2023A/GHS_BUILT_H_ANBH_E2018_GLOBE_R2023A_54009_10/V1-0/"
)

# Alternative: direct tile download for known tile indices.
# Tiles follow Mollweide 10°×10° grid. Converting bbox → tile index is complex.
# Instead, we provide a simpler approach using the REST endpoint.

# EU Building Height WCS (alternative, simpler for Europe):
_EU_WCS_URL = (
    "https://image.discomap.eea.europa.eu/arcgis/services/"
    "GioLand/BuildingHeight2012/MapServer/WCSServer"
)


def _is_in_europe(bbox: BBox) -> bool:
    """Check if bbox overlaps the European coverage area."""
    north, south, east, west = bbox
    # Rough European bounds (including Turkey, Iceland, parts of Russia)
    return (south < 72 and north > 34 and west < 45 and east > -25)


def _fetch_eu_wcs(bbox: BBox, dim: Tuple[int, int]) -> np.ndarray | None:
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


def _fetch_ghsl_tiles(bbox: BBox, dim: Tuple[int, int]) -> np.ndarray | None:
    """Fetch GHSL GHS-BUILT-H tiles (global, 100m).

    This is the fallback for non-European areas.
    Uses a simplified approach: download pre-computed tiles from JRC FTP.
    """
    # GHSL tiles are in Mollweide projection with complex naming.
    # For now, return None — this will be implemented when we need
    # non-European coverage from GHSL specifically.
    # Other providers (WSF3D, nDSM) cover the global case.
    logger.debug("GHSL global tile fetch not yet implemented")
    return None


def _parse_geotiff_bytes(data: bytes) -> np.ndarray | None:
    """Parse GeoTIFF bytes into a float32 array."""
    try:
        import rasterio
        try:
            with rasterio.open(io.BytesIO(data)) as src:
                arr = src.read(1).astype(np.float32)
                nodata = src.nodata
                if nodata is not None:
                    arr[arr == nodata] = np.nan
                # Zero height = no building
                arr[arr <= 0] = np.nan
                return arr
        except rasterio.errors.RasterioIOError:
            logger.warning("Failed to parse GeoTIFF with rasterio")
            return None
    except ImportError:
        pass

    # Fallback without rasterio: try PIL
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        arr = np.array(img, dtype=np.float32)
        arr[arr <= 0] = np.nan
        return arr
    except Exception as e:
        logger.warning(f"Cannot parse GeoTIFF bytes: {e}")
        return None


class CopernicusProvider:
    """Copernicus EU Building Height (10m, Europe) + GHSL fallback."""

    name = "copernicus"

    def covers(self, bbox: BBox) -> bool:
        """Returns True for European bboxes (primary coverage area)."""
        return _is_in_europe(bbox)

    def fetch_heights(self, bbox: BBox, dim: Tuple[int, int]) -> HeightResult:
        """Fetch building heights from Copernicus/GHSL sources."""
        north, south, east, west = bbox
        cache_key = make_cache_key(_NAMESPACE, north, south, east, west,
                                   {"dim": list(dim)})

        # Check cache
        cached = read_array_cache(_NAMESPACE, cache_key)
        if cached is not None:
            arrays, meta = cached
            return HeightResult(
                raster=arrays["raster"],
                confidence=arrays["confidence"],
                source_name=self.name,
                resolution_m=meta.get("resolution_m", _RESOLUTION_M),
            )

        raster = None
        resolution = _RESOLUTION_M

        # Try EU WCS first (better resolution)
        if _is_in_europe(bbox):
            raster = _fetch_eu_wcs(bbox, dim)

        # Fallback to GHSL global
        if raster is None:
            raster = _fetch_ghsl_tiles(bbox, dim)
            resolution = 100.0

        if raster is None:
            return _empty_result(dim)

        # Resample to target dim if needed
        if raster.shape != dim:
            raster = _resample(raster, dim)

        confidence = np.where(
            np.isnan(raster), 0.0, _CONFIDENCE
        ).astype(np.float32)

        result = HeightResult(raster, confidence, self.name, resolution)

        # Cache
        write_array_cache(_NAMESPACE, cache_key,
                          {"raster": raster, "confidence": confidence},
                          {"resolution_m": resolution})
        return result


def _empty_result(dim: Tuple[int, int]) -> HeightResult:
    h, w = dim
    return HeightResult(
        raster=np.full((h, w), np.nan, dtype=np.float32),
        confidence=np.zeros((h, w), dtype=np.float32),
        source_name="copernicus",
        resolution_m=_RESOLUTION_M,
    )
